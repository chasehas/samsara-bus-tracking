"""
Geofence & Alert State Engine
Supports multiple time-windowed profiles (e.g. Morning vs Afternoon runs)
with progressive/staged checkpoints (e.g. Approach -> Subdivision -> Street)
and sequential progression gating to prevent premature alerts.
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("geofence")

EARTH_RADIUS_METERS = 6371000.0
METERS_PER_MILE = 1609.344


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in meters between two coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


def parse_time_str(t_str: str) -> dtime:
    parts = t_str.strip().split(":")
    return dtime(int(parts[0]), int(parts[1]))


@dataclass
class TriggerCheckpoint:
    name: str
    lat: float
    lon: float
    radius_meters: float = 100.0
    min_speed_mph: float = 3.0  # Must be moving (filters out parking lot GPS drift)
    title: Optional[str] = None
    message: Optional[str] = None
    priority: str = "high"
    tags: List[str] = field(default_factory=lambda: ["bus"])
    fired: bool = False
    fired_time: Optional[float] = None

    def evaluate(
        self,
        lat: float,
        lon: float,
        timestamp_sec: Optional[float] = None,
        speed_mph: Optional[float] = None,
    ) -> Tuple[bool, float]:
        """
        Returns (should_fire, distance_meters).
        Does NOT mutate self.fired; caller must call mark_fired() upon confirmed delivery.
        """
        dist = haversine_distance(lat, lon, self.lat, self.lon)
        if self.fired or dist > self.radius_meters:
            return False, dist

        # Speed filter (filter stationary GPS drift)
        if self.min_speed_mph > 0:
            if speed_mph is None or speed_mph < self.min_speed_mph:
                return False, dist

        return True, dist

    def mark_fired(self, timestamp_sec: Optional[float] = None):
        """Marks the checkpoint as successfully fired."""
        self.fired = True
        self.fired_time = timestamp_sec or datetime.now(timezone.utc).timestamp()

    def reset(self):
        self.fired = False
        self.fired_time = None


@dataclass
class RouteProfile:
    name: str
    window_start: Optional[dtime] = None
    window_end: Optional[dtime] = None
    triggers: List[TriggerCheckpoint] = field(default_factory=list)
    require_sequential: bool = True
    cooldown_seconds: float = 7200.0  # 2 hours
    timezone: str = "America/New_York"

    def is_time_active(self, current_dt: Optional[datetime] = None) -> bool:
        """
        Evaluates whether the profile is active according to the configured timezone.
        """
        if self.window_start is None or self.window_end is None:
            return True

        if current_dt is None:
            try:
                tz = ZoneInfo(self.timezone)
                current_dt = datetime.now(tz)
            except Exception as e:
                logger.warning(f"Could not load timezone '{self.timezone}': {e}. Using local time.")
                current_dt = datetime.now()

        current_t = current_dt.time()
        return self.window_start <= current_t <= self.window_end

    def check_cooldown_reset(self, now_ts: float):
        """Resets triggers if the cooldown period from the last alert has elapsed."""
        fired_times = [t.fired_time for t in self.triggers if t.fired_time is not None]
        if fired_times:
            last_fired = max(fired_times)
            if (now_ts - last_fired) > self.cooldown_seconds:
                logger.info(f"[{self.name}] Cooldown expired. Resetting all checkpoints for next run.")
                self.reset()

    def evaluate_all_triggers(
        self,
        lat: float,
        lon: float,
        heading: Optional[float] = None,
        speed: Optional[float] = None,
        timestamp_sec: Optional[float] = None,
    ) -> Tuple[List[Tuple[TriggerCheckpoint, float]], Dict[str, Any]]:
        """
        Evaluates coordinates against checkpoints in this profile.
        Returns a list of (trigger, distance_m) that are ready to fire, and checkpoint statuses.
        """
        now_ts = timestamp_sec or datetime.now(timezone.utc).timestamp()
        self.check_cooldown_reset(now_ts)

        fired_now: List[Tuple[TriggerCheckpoint, float]] = []
        checkpoint_statuses = []

        if self.require_sequential:
            # Only evaluate the next unfired trigger
            next_unfired = next((t for t in self.triggers if not t.fired), None)
            for trig in self.triggers:
                dist = haversine_distance(lat, lon, trig.lat, trig.lon)
                did_fire = False
                if trig is next_unfired:
                    did_fire, _ = trig.evaluate(lat, lon, now_ts, speed_mph=speed)
                    if did_fire:
                        fired_now.append((trig, dist))
                checkpoint_statuses.append({
                    "name": trig.name,
                    "fired": trig.fired,
                    "is_active_target": (trig is next_unfired),
                    "dist_m": round(dist, 1),
                    "dist_mi": round(dist / METERS_PER_MILE, 2),
                })
        else:
            # Independent triggers
            for trig in self.triggers:
                did_fire, dist_m = trig.evaluate(lat, lon, now_ts, speed_mph=speed)
                if did_fire:
                    fired_now.append((trig, dist_m))
                checkpoint_statuses.append({
                    "name": trig.name,
                    "fired": trig.fired,
                    "is_active_target": not trig.fired,
                    "dist_m": round(dist_m, 1),
                    "dist_mi": round(dist_m / METERS_PER_MILE, 2),
                })

        status_summary = {
            "profile": self.name,
            "checkpoints": checkpoint_statuses,
        }
        return fired_now, status_summary

    def reset(self):
        for t in self.triggers:
            t.reset()
