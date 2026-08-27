"""
Bus Route Telemetry Poller & Recorder

Polls Samsara Fleet Viewer for vehicle location data at a configurable interval
and records updates to JSONL and CSV files for analysis.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import logging
import os
import signal
import sys
import time
from typing import Any, Dict, Optional, Set

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from samsara_client import SamsaraClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tracker")

DEFAULT_DURATION_MS = 30000
DEFAULT_POLL_INTERVAL_SEC = 10.0


def format_timestamp(ms: int) -> str:
    """Convert millisecond epoch timestamp to ISO 8601 string."""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()


class RouteRecorder:
    def __init__(
        self,
        token: str,
        output_dir: str = "logs",
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        query_duration_ms: int = DEFAULT_DURATION_MS,
    ):
        self.token = token
        self.output_dir = output_dir
        self.poll_interval_sec = poll_interval_sec
        self.query_duration_ms = query_duration_ms
        self.client = SamsaraClient(token=token)

        self.running = False
        self._seen_points: Set[str] = set()
        self._last_location: Optional[Dict] = None

        os.makedirs(self.output_dir, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.jsonl_path = os.path.join(self.output_dir, f"route_{today_str}.jsonl")
        self.csv_path = os.path.join(self.output_dir, f"route_{today_str}.csv")

        self._init_csv()

    def _init_csv(self):
        """Write CSV header if file does not exist yet."""
        if not os.path.exists(self.csv_path) or os.path.getsize(self.csv_path) == 0:
            with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "recorded_at",
                    "device_id",
                    "device_name",
                    "time_ms",
                    "iso_time",
                    "latitude",
                    "longitude",
                    "heading",
                    "speed_mph",
                    "formatted_address",
                ])

    def record_point(self, device_id: Any, device_name: str, loc: Dict):
        """Record a single location data point if not seen before."""
        time_ms = loc.get("time")
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        heading = loc.get("heading")
        speed = loc.get("speed")
        address = loc.get("formatted", "")

        # Unique key to deduplicate identical samples
        point_key = f"{device_id}_{time_ms}_{lat}_{lon}"
        if point_key in self._seen_points:
            return False

        self._seen_points.add(point_key)
        now_iso = datetime.now(timezone.utc).isoformat()
        iso_time = format_timestamp(time_ms) if time_ms else now_iso

        record = {
            "recorded_at": now_iso,
            "device_id": device_id,
            "device_name": device_name,
            "time_ms": time_ms,
            "iso_time": iso_time,
            "latitude": lat,
            "longitude": lon,
            "heading": heading,
            "speed_mph": speed,
            "formatted_address": address,
        }

        # Append to JSONL
        with open(self.jsonl_path, mode="a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # Append to CSV
        with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                now_iso,
                device_id,
                device_name,
                time_ms,
                iso_time,
                lat,
                lon,
                heading,
                f"{speed:.2f}" if isinstance(speed, (int, float)) else speed,
                address,
            ])

        logger.info(
            f"📍 [{device_name}] Lat: {lat:.6f}, Lon: {lon:.6f} | "
            f"Speed: {speed:.1f} mph | Heading: {heading}° | Near: {address}"
        )
        return True

    def poll_once(self) -> int:
        """Poll once and record any new location points. Returns count of new points."""
        devices = self.client.get_latest_locations(duration_ms=self.query_duration_ms)
        new_points = 0

        for dev in devices:
            dev_id = dev.get("id")
            dev_name = dev.get("name", "Unknown")
            locations = dev.get("location", [])
            for loc in locations:
                if self.record_point(dev_id, dev_name, loc):
                    new_points += 1

        return new_points

    def run(self):
        """Continuously poll at interval until stopped."""
        self.running = True
        logger.info(f"Starting bus route recorder...")
        logger.info(f"Viewer Token: {self.token}")
        logger.info(f"Polling Interval: {self.poll_interval_sec}s")
        logger.info(f"Saving to: {self.jsonl_path} and {self.csv_path}")

        consecutive_errors = 0

        while self.running:
            try:
                new_pts = self.poll_once()
                if new_pts == 0:
                    logger.debug("No new location updates since last poll.")
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Polling error ({consecutive_errors} in a row): {e}")
                if consecutive_errors > 10:
                    logger.critical("Too many consecutive errors. Backing off for 60s...")
                    time.sleep(60)

            time.sleep(self.poll_interval_sec)

    def stop(self):
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="Poll and record Samsara bus tracking telemetry.")
    parser.add_argument("--token", default=os.getenv("SAMSARA_TOKEN", ""), help="Samsara Fleet Viewer token")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_SEC, help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL_SEC})")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_MS, help=f"GraphQL query duration window in ms (default: {DEFAULT_DURATION_MS})")
    parser.add_argument("--output-dir", default="logs", help="Directory to save route logs (default: logs)")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if not args.token:
        logger.error("No Samsara token provided. Use --token or set SAMSARA_TOKEN env var.")
        sys.exit(1)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    recorder = RouteRecorder(
        token=args.token,
        output_dir=args.output_dir,
        poll_interval_sec=args.interval,
        query_duration_ms=args.duration,
    )

    def handle_sig(sig, frame):
        logger.info("Termination signal received. Shutting down cleanly...")
        recorder.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    if args.once:
        recorder.poll_once()
    else:
        recorder.run()


if __name__ == "__main__":
    main()
