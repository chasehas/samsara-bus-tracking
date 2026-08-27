"""
School Bus Live Tracker & Geofence Alert Service

Periodically polls Samsara Fleet Viewer for school bus coordinates,
evaluates staged geofencing checkpoints (e.g. Approach -> Subdivision -> Street),
dispatches instant push notifications via ntfy.sh to mobile devices,
and serves a minimal, unauthenticated /health check for cloud platforms.

Supports configuration via:
1. config.json file (default)
2. CONFIG_JSON environment variable (full JSON payload)
3. Individual environment variables (SAMSARA_TOKEN, NTFY_TOPIC, etc.)
"""

import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from geofence import RouteProfile, TriggerCheckpoint, parse_time_str
from notifier import NtfyNotifier
from samsara_client import SamsaraClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bus_alert")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


def format_timestamp(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()


def format_speed(speed: Optional[float]) -> str:
    if isinstance(speed, (int, float)):
        return f"{speed:.1f}mph"
    return "N/A"


def format_heading(heading: Optional[float]) -> str:
    if isinstance(heading, (int, float)):
        return f"{heading:.0f}°"
    return "N/A"


class HealthCheckHandler(BaseHTTPRequestHandler):
    """
    Minimal health check handler.
    Returns strictly generic status (no tokens, no coordinates, no addresses).
    """
    service_ref: Optional["BusAlertService"] = None

    def do_GET(self):
        service = self.service_ref
        now_iso = datetime.now(timezone.utc).isoformat()

        is_healthy = True
        error_msg = None

        if service:
            active_p = service.get_active_profile()
            now_t = time.time()

            # If inside active window, verify that polling is actively succeeding
            if active_p:
                if service.last_poll_success_ts == 0.0:
                    # Never succeeded: give 120s grace period from startup before failing healthcheck
                    if (now_t - service.start_time) > 120:
                        is_healthy = False
                        error_msg = "Never completed a successful polling cycle since service startup"
                else:
                    elapsed = now_t - service.last_poll_success_ts
                    if elapsed > 300:
                        is_healthy = False
                        error_msg = f"Polling stalled (last success {int(elapsed)}s ago)"

        status_code = 200 if is_healthy else 503
        response_data = {
            "status": "healthy" if is_healthy else "degraded",
            "service": "school-bus-tracker",
            "timestamp": now_iso,
        }
        if error_msg:
            response_data["error"] = error_msg
        elif service and service.last_poll_error:
            # Include sanitized last error notice if present
            response_data["notice"] = "Transient polling warning recorded"

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response_data).encode("utf-8"))

    def log_message(self, format, *args):
        # Silence access logs to keep terminal clear
        return


def start_health_server(service: "BusAlertService", port: int = 10000):
    """Starts the minimal health check server on a background daemon thread."""
    HealthCheckHandler.service_ref = service
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Health server listening on port {port} (/health)")
    except Exception as e:
        logger.warning(f"Could not start HTTP health server on port {port}: {e}")


class BusAlertService:
    def __init__(self, config_dict: dict, log_dir: Optional[str] = None):
        self.config_dict = config_dict
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.state_file = os.path.join(self.log_dir, ".service_state.json")
        self.timezone_name = config_dict.get("timezone", "America/New_York")
        self.target_device_name = config_dict.get("target_device_name") or os.getenv("TARGET_DEVICE_NAME")
        if self.target_device_name:
            self.target_device_name = str(self.target_device_name).strip()

        self.start_time: float = time.time()
        self.last_poll_success_ts: float = 0.0
        self.last_poll_error: Optional[str] = None

        # Safely attempt to initialize log directory
        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create log directory '%s': %s (Disk logging will be disabled)", self.log_dir, e)

        # Samsara Client
        self.token = config_dict["samsara_token"]
        self.client = SamsaraClient(token=self.token)
        self.poll_interval = float(config_dict.get("poll_interval_sec", 8.0))
        self.query_duration_ms = int(config_dict.get("query_duration_ms", 30000))

        # Build Route Profiles
        self.profiles: List[RouteProfile] = []
        raw_profiles = config_dict.get("profiles", [])

        # Backward compatibility for single geofence definition
        if not raw_profiles and "geofence" in config_dict:
            geo_cfg = config_dict["geofence"]
            raw_profiles = [{
                "name": "Default Run",
                "require_sequential": False,
                "cooldown_seconds": geo_cfg.get("cooldown_seconds", 7200.0),
                "triggers": [
                    {
                        "name": "Target Stop",
                        "lat": geo_cfg["stop_lat"],
                        "lon": geo_cfg["stop_lon"],
                        "radius_meters": geo_cfg.get("trigger_radius_meters", 400.0),
                        "min_speed_mph": geo_cfg.get("min_speed_mph", 3.0),
                    }
                ]
            }]

        for p_cfg in raw_profiles:
            w_start, w_end = None, None
            if "window" in p_cfg and "-" in p_cfg["window"]:
                parts = p_cfg["window"].split("-")
                w_start = parse_time_str(parts[0])
                w_end = parse_time_str(parts[1])

            triggers = []
            for t_cfg in p_cfg.get("triggers", []):
                trig = TriggerCheckpoint(
                    name=t_cfg["name"],
                    lat=float(t_cfg["lat"]),
                    lon=float(t_cfg["lon"]),
                    radius_meters=float(t_cfg.get("radius_meters", 100.0)),
                    min_speed_mph=float(t_cfg.get("min_speed_mph", 3.0)),
                    title=t_cfg.get("title"),
                    message=t_cfg.get("message"),
                    priority=t_cfg.get("priority", "high"),
                    tags=t_cfg.get("tags", ["bus"]),
                )
                triggers.append(trig)

            profile = RouteProfile(
                name=p_cfg.get("name", "Route Profile"),
                window_start=w_start,
                window_end=w_end,
                triggers=triggers,
                require_sequential=p_cfg.get("require_sequential", False),
                cooldown_seconds=float(p_cfg.get("cooldown_seconds", 7200.0)),
                timezone=p_cfg.get("timezone", self.timezone_name),
            )
            self.profiles.append(profile)

        # Restore any persisted checkpoint state from previous runs/restarts
        self.load_persisted_state()

        # ntfy Notifier
        ntfy_cfg = config_dict.get("ntfy", {})
        self.topic = ntfy_cfg.get("topic", "").strip()
        self.notifier = NtfyNotifier(
            topic=self.topic,
            server_url=ntfy_cfg.get("server_url", "https://ntfy.sh"),
        ) if self.topic else None

        self._seen_points: Set[str] = set()
        self.running = False

    def load_persisted_state(self):
        """Restores fired checkpoints from disk if present."""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now_ts = time.time()
            for profile in self.profiles:
                if profile.name in data:
                    profile.import_state(data[profile.name], now_ts=now_ts)
        except Exception as e:
            logger.debug("Could not restore persisted state: %s", e)

    def save_persisted_state(self):
        """Saves current fired checkpoint timestamps to disk."""
        try:
            state_data = {profile.name: profile.export_state() for profile in self.profiles}
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
        except Exception as e:
            logger.debug("Could not save persisted state: %s", e)

    def get_active_profile(self) -> Optional[RouteProfile]:
        """Returns the currently active profile based on configured timezone."""
        for p in self.profiles:
            if p.is_time_active():
                return p
        return None

    def log_telemetry_point(self, dev_id: str, dev_name: str, loc: dict):
        """
        Safely appends telemetry points to daily CSV and JSONL log files.
        Wrapped in try/except so disk/drive errors never interrupt live alert evaluation.
        """
        try:
            time_ms = loc.get("time")
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            heading = loc.get("heading")
            speed = loc.get("speed")
            address = loc.get("formatted", "")

            key = f"{dev_id}_{time_ms}_{lat}_{lon}"
            if key in self._seen_points:
                return
            self._seen_points.add(key)

            today_str = datetime.now().strftime("%Y-%m-%d")
            csv_path = os.path.join(self.log_dir, f"route_{today_str}.csv")
            jsonl_path = os.path.join(self.log_dir, f"route_{today_str}.jsonl")

            now_iso = datetime.now(timezone.utc).isoformat()
            iso_time = format_timestamp(time_ms) if time_ms else now_iso

            # Ensure CSV header
            if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "recorded_at", "device_id", "device_name", "time_ms",
                        "iso_time", "latitude", "longitude", "heading", "speed_mph", "formatted_address"
                    ])

            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    now_iso, dev_id, dev_name, time_ms, iso_time,
                    lat, lon,
                    f"{heading:.0f}" if isinstance(heading, (int, float)) else "",
                    f"{speed:.2f}" if isinstance(speed, (int, float)) else "",
                    address
                ])

            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "recorded_at": now_iso, "device_id": dev_id, "device_name": dev_name,
                    "time_ms": time_ms, "iso_time": iso_time, "latitude": lat, "longitude": lon,
                    "heading": heading, "speed_mph": speed, "formatted_address": address
                }) + "\n")
        except Exception as e:
            logger.debug("Disk logging skipped due to error: %s", e)

    def dispatch_alert(
        self,
        profile: RouteProfile,
        trigger: TriggerCheckpoint,
        device_name: str,
        dist_meters: float,
        formatted_address: str,
    ) -> bool:
        """
        Dispatches push notification via ntfy.
        Returns True if delivery succeeded, False otherwise.
        """
        if not self.notifier:
            logger.warning("No ntfy topic configured. Skipping notification dispatch.")
            return False

        title = trigger.title or f"Bus Alert: {trigger.name} 🚌"
        custom_msg = trigger.message
        if custom_msg:
            msg = f"{custom_msg}\nNear: {formatted_address}"
        else:
            dist_mi = dist_meters / 1609.344
            msg = f"Bus {device_name} reached {trigger.name} ({dist_mi:.2f} mi away)!\nNear: {formatted_address}"

        click_url = f"https://cloud.samsara.com/fleet/viewer/{self.client.token}"
        return self.notifier.send_alert(
            message=msg,
            title=title,
            priority=trigger.priority,
            tags=trigger.tags,
            click_url=click_url,
        )

    def tick(self):
        active_profile = self.get_active_profile()
        if not active_profile:
            logger.debug("Outside active profile windows. Sleeping...")
            return

        devices = self.client.get_latest_locations(duration_ms=self.query_duration_ms)

        # Warn if multiple devices exist without a target filter
        if len(devices) > 1 and not self.target_device_name:
            logger.warning(
                f"⚠️ Multiple vehicles ({len(devices)}) returned in viewer, but no 'target_device_name' is set in config! "
                "All vehicles will evaluate against the same profile. Consider setting 'target_device_name' to avoid vehicle conflicts."
            )

        for dev in devices:
            dev_id = str(dev.get("id", ""))
            dev_name = str(dev.get("name", "Bus"))

            # Optional filter by target device name or ID
            if self.target_device_name:
                if dev_name != self.target_device_name and dev_id != self.target_device_name:
                    logger.debug("Skipping non-target device '%s' (id: %s)", dev_name, dev_id)
                    continue

            locations = dev.get("location", [])

            for loc in locations:
                lat = loc.get("latitude")
                lon = loc.get("longitude")
                if lat is None or lon is None:
                    continue

                # Safely log telemetry to disk
                self.log_telemetry_point(dev_id, dev_name, loc)

                heading = loc.get("heading")
                speed = loc.get("speed")
                address = loc.get("formatted", "")
                time_ms = loc.get("time")
                ts_sec = (time_ms / 1000.0) if time_ms else None

                fired_triggers, status = active_profile.evaluate_all_triggers(
                    lat=lat,
                    lon=lon,
                    heading=heading,
                    speed=speed,
                    timestamp_sec=ts_sec,
                )

                # Format status logging safely
                speed_str = format_speed(speed)
                head_str = format_heading(heading)
                cp_str = " | ".join([
                    f"{c['name'][:18]}: {'[FIRED]' if c['fired'] else (f'[{c['dist_m']}m*]' if c.get('is_active_target') else f'{c['dist_m']}m')}"
                    for c in status["checkpoints"]
                ])
                logger.info(f"[{active_profile.name} | Bus {dev_name}] Speed: {speed_str} | Head: {head_str} | {cp_str}")

                for trig, dist_m in fired_triggers:
                    logger.info(f"🚨 CHECKPOINT REACHED: '{trig.name}' ({dist_m:.0f}m)! DISPATCHING ALERT!")
                    success = self.dispatch_alert(active_profile, trig, dev_name, dist_m, address)
                    if success:
                        trig.mark_fired(ts_sec)
                        self.save_persisted_state()
                        logger.info(f"✅ Alert for '{trig.name}' successfully delivered and marked fired.")
                    else:
                        logger.warning(f"⚠️ Delivery failed for '{trig.name}'. Will retry on next tick while in range.")

        # Update last successful poll timestamp at the end of a fully completed cycle
        self.last_poll_success_ts = time.time()
        self.last_poll_error = None

    def run(self):
        self.running = True
        logger.info(f"Starting Bus Alert Service (Timezone: {self.timezone_name})...")
        if self.target_device_name:
            logger.info(f"  Target Bus Filter: '{self.target_device_name}'")
        for p in self.profiles:
            w_str = f"[{p.window_start.strftime('%H:%M')} - {p.window_end.strftime('%H:%M')}]" if p.window_start else "[All Day]"
            seq_str = "Sequential" if p.require_sequential else "Independent"
            logger.info(f"  Profile '{p.name}' {w_str} ({seq_str}, TZ: {p.timezone}):")
            for t in p.triggers:
                logger.info(f"    - {t.name}: ({t.lat:.6f}, {t.lon:.6f}) Radius: {t.radius_meters}m | MinSpeed: {t.min_speed_mph}mph | Priority: {t.priority}")

        if self.topic:
            logger.info(f"  ntfy Topic: https://ntfy.sh/{self.topic}")
        else:
            logger.warning("  No ntfy topic configured!")

        while self.running:
            try:
                self.tick()
            except Exception as e:
                self.last_poll_error = str(e)
                logger.error(f"Error during polling tick: {e}", exc_info=True)

            time.sleep(self.poll_interval)

    def stop(self):
        self.running = False


def load_config(config_path_or_default: str = "config.json") -> dict:
    """
    Loads configuration with the following priority:
    1. CONFIG_JSON environment variable (full JSON payload string)
    2. Path specified in CONFIG_PATH env variable or config_path_or_default argument
    3. Individual environment variables (SAMSARA_TOKEN, NTFY_TOPIC, etc.)
    """
    # 1. Check raw JSON string in environment variable
    env_json = os.getenv("CONFIG_JSON")
    if env_json and env_json.strip():
        logger.info("Loading configuration from CONFIG_JSON environment variable.")
        return json.loads(env_json)

    # 2. Check config file
    target_path = os.getenv("CONFIG_PATH", config_path_or_default)
    if not os.path.isabs(target_path):
        if not os.path.exists(target_path):
            script_relative = os.path.join(SCRIPT_DIR, target_path)
            if os.path.exists(script_relative):
                target_path = script_relative

    if os.path.exists(target_path):
        logger.info(f"Loading configuration from file: {target_path}")
        with open(target_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 3. Check individual environment variables
    samsara_token = os.getenv("SAMSARA_TOKEN")
    if samsara_token:
        logger.info("Building configuration from individual environment variables.")
        return {
            "samsara_token": samsara_token,
            "timezone": os.getenv("TIMEZONE", "America/New_York"),
            "target_device_name": os.getenv("TARGET_DEVICE_NAME"),
            "poll_interval_sec": float(os.getenv("POLL_INTERVAL_SEC", "8.0")),
            "query_duration_ms": int(os.getenv("QUERY_DURATION_MS", "30000")),
            "profiles": [
                {
                    "name": "Default Run",
                    "window": os.getenv("SCHEDULE_WINDOW", "06:45-17:00"),
                    "require_sequential": False,
                    "triggers": [
                        {
                            "name": os.getenv("TRIGGER_NAME", "Bus Stop"),
                            "lat": float(os.getenv("TRIGGER_LAT", "0.0")),
                            "lon": float(os.getenv("TRIGGER_LON", "0.0")),
                            "radius_meters": float(os.getenv("TRIGGER_RADIUS_METERS", "150.0")),
                            "min_speed_mph": float(os.getenv("MIN_SPEED_MPH", "3.0")),
                        }
                    ]
                }
            ],
            "ntfy": {
                "topic": os.getenv("NTFY_TOPIC", ""),
                "server_url": os.getenv("NTFY_SERVER", "https://ntfy.sh"),
            }
        }

    raise FileNotFoundError(
        f"No configuration found! Create a '{config_path_or_default}' file or set the CONFIG_JSON environment variable."
    )


def main():
    parser = argparse.ArgumentParser(description="Run School Bus Geofence & Notification Service.")
    parser.add_argument("--config", default="config.json", help="Path to config JSON file (default: config.json)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "10000")), help="HTTP health server port (default: 10000)")
    args = parser.parse_args()

    config = load_config(args.config)
    service = BusAlertService(config)

    # Start minimal health check server on background thread (for Render / Fly.io / Cloud healthchecks)
    start_health_server(service, port=args.port)

    def handle_sig(sig, frame):
        logger.info("Stopping bus alert service...")
        service.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    service.run()


if __name__ == "__main__":
    main()
