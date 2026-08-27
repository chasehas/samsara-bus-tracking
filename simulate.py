"""
Route & Geofence Simulator

Replays recorded CSV/JSONL telemetry through staged RouteProfiles
to verify and fine-tune trigger radii, checkpoint sequences, and notification timing.
"""

import argparse
import csv
import json
import os
import sys
from typing import Dict, List

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from geofence import RouteProfile, TriggerCheckpoint, parse_time_str


def load_points_from_csv(csv_path: str) -> List[Dict]:
    points = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                points.append({
                    "iso_time": row.get("iso_time") or row.get("recorded_at"),
                    "device_name": row.get("device_name", ""),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "heading": float(row["heading"]) if row.get("heading") else None,
                    "speed": float(row["speed_mph"]) if row.get("speed_mph") else 0.0,
                    "formatted_address": row.get("formatted_address", ""),
                    "time_ms": int(row["time_ms"]) if row.get("time_ms") else None,
                })
            except (ValueError, KeyError):
                continue
    return points


def run_simulation(points: List[Dict], profile: RouteProfile):
    print("\n" + "="*85)
    print(f"SIMULATION RUN: [{profile.name}] (Sequential: {profile.require_sequential})")
    print(f"Configured Checkpoints ({len(profile.triggers)}):")
    for t in profile.triggers:
        print(f"  - {t.name}: ({t.lat:.6f}, {t.lon:.6f}) Radius: {t.radius_meters}m | Priority: {t.priority}")
    print(f"Total Telemetry Points to Replay: {len(points)}")
    print("="*85)

    total_alerts_fired = 0

    for i, p in enumerate(points):
        ts = (p["time_ms"] / 1000.0) if p["time_ms"] else None
        fired_now, status = profile.evaluate_all_triggers(
            lat=p["latitude"],
            lon=p["longitude"],
            heading=p["heading"],
            speed=p["speed"],
            timestamp_sec=ts,
        )

        cp_str = " | ".join([
            f"{c['name'][:15]}: {'[FIRED]' if c['fired'] else (f'[{c['dist_m']}m*]' if c.get('is_active_target') else f'{c['dist_m']}m')}"
            for c in status["checkpoints"]
        ])
        print(f"[{i+1:02d}] {p['iso_time']} | Speed: {p['speed']:.1f}mph | Head: {p['heading']}° | {cp_str}")

        for trig, dist_m in fired_now:
            total_alerts_fired += 1
            print("*"*85)
            print(f"🚨 CHECKPOINT ALERT: '{trig.name}' at {p['iso_time']}!")
            print(f"   Bus Location: {p['latitude']:.6f}, {p['longitude']:.6f} (dist: {dist_m:.0f}m)")
            print(f"   Address:      {p['formatted_address']}")
            print(f"   Title:        {trig.title}")
            print(f"   Message:      {trig.message}")
            print(f"   Priority:     {trig.priority} | Tags: {trig.tags}")
            print("*"*85)

    print("\n" + "="*85)
    print(f"Simulation Finished. Total alerts fired: {total_alerts_fired} of {len(profile.triggers)} checkpoints.")
    print("="*85 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Simulate route playback through staged checkpoint triggers.")
    parser.add_argument("file", help="Path to CSV log file (e.g. logs/route_2026-08-18.csv)")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--profile", default="Afternoon Run", help="Profile name to test (e.g. 'Morning Run' or 'Afternoon Run')")

    args = parser.parse_args()

    points = load_points_from_csv(args.file)
    if not points:
        print(f"Error: No valid points found in {args.file}")
        sys.exit(1)

    if not os.path.exists(args.config):
        print(f"Config file {args.config} not found.")
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    matched = [p for p in cfg.get("profiles", []) if p.get("name", "").lower() == args.profile.lower()]
    if not matched:
        print(f"Profile '{args.profile}' not found in {args.config}. Available: {[p.get('name') for p in cfg.get('profiles', [])]}")
        sys.exit(1)

    p_cfg = matched[0]
    triggers = []
    for t_cfg in p_cfg.get("triggers", []):
        triggers.append(TriggerCheckpoint(
            name=t_cfg["name"],
            lat=float(t_cfg["lat"]),
            lon=float(t_cfg["lon"]),
            radius_meters=float(t_cfg.get("radius_meters", 100.0)),
            min_speed_mph=float(t_cfg.get("min_speed_mph", 3.0)),
            title=t_cfg.get("title"),
            message=t_cfg.get("message"),
            priority=t_cfg.get("priority", "high"),
            tags=t_cfg.get("tags", ["bus"]),
        ))

    profile = RouteProfile(
        name=p_cfg.get("name", "Route Profile"),
        triggers=triggers,
        require_sequential=p_cfg.get("require_sequential", True),
        cooldown_seconds=float(p_cfg.get("cooldown_seconds", 7200.0)),
    )

    run_simulation(points, profile)


if __name__ == "__main__":
    main()
