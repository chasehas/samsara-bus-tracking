"""
HAR File Parser
Extracts Samsara FleetViewer GraphQL responses from a browser HAR export.
"""

import csv
from datetime import datetime, timezone
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("har_parser")


def format_timestamp(ms: int) -> str:
    """Convert millisecond epoch timestamp to ISO 8601 string."""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()


def parse_har(har_path: str, output_csv: str = "logs/har_extracted_route.csv", output_jsonl: str = "logs/har_extracted_route.jsonl"):
    logger.info(f"Opening HAR file: {har_path} (size: {os.path.getsize(har_path) / (1024*1024):.2f} MB)...")
    
    with open(har_path, "r", encoding="utf-8", errors="ignore") as f:
        har_data = json.load(f)

    entries = har_data.get("log", {}).get("entries", [])
    logger.info(f"Total network entries in HAR: {len(entries)}")

    points = []
    seen_keys = set()
    parsed_responses = 0

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")

        # Look specifically for Samsara GraphQL / FleetViewer API requests
        if "/r/graphql" not in url:
            continue

        resp = entry.get("response", {})
        content = resp.get("content", {})
        text = content.get("text", "")

        if not text:
            continue

        try:
            res_json = json.loads(text)
        except Exception:
            continue

        parsed_responses += 1

        # Check for FleetViewer GraphQL structure
        data_root = res_json.get("output") or res_json.get("data")
        if not isinstance(data_root, dict):
            continue

        token_data = data_root.get("fleetViewerToken")
        if not isinstance(token_data, dict):
            continue

        devices = token_data.get("devices", [])
        if not isinstance(devices, list):
            continue

        for dev in devices:
            if not isinstance(dev, dict):
                continue
            dev_id = dev.get("id")
            dev_name = dev.get("name", "Unknown")
            locations = dev.get("location", [])

            if isinstance(locations, dict):
                locations = [locations]
            elif not isinstance(locations, list):
                continue

            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                time_ms = loc.get("time")
                lat = loc.get("latitude")
                lon = loc.get("longitude")
                heading = loc.get("heading")
                speed = loc.get("speed")
                address = loc.get("formatted", "")

                key = f"{dev_id}_{time_ms}_{lat}_{lon}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                iso_time = format_timestamp(time_ms) if time_ms else entry.get("startedDateTime", "")
                points.append({
                    "device_id": dev_id,
                    "device_name": dev_name,
                    "time_ms": time_ms,
                    "iso_time": iso_time,
                    "latitude": lat,
                    "longitude": lon,
                    "heading": heading,
                    "speed_mph": speed,
                    "formatted_address": address,
                    "http_request_time": entry.get("startedDateTime", ""),
                })

    # Sort points chronologically by time_ms
    points.sort(key=lambda x: x["time_ms"] or 0)
    logger.info(f"Examined {parsed_responses} GraphQL responses. Extracted {len(points)} unique telemetry points.")

    if not points:
        logger.warning("No location points found in HAR.")
        return []

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    # Write CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "iso_time",
            "device_id",
            "device_name",
            "time_ms",
            "latitude",
            "longitude",
            "heading",
            "speed_mph",
            "formatted_address",
            "http_request_time",
        ])
        for p in points:
            speed = p["speed_mph"]
            writer.writerow([
                p["iso_time"],
                p["device_id"],
                p["device_name"],
                p["time_ms"],
                p["latitude"],
                p["longitude"],
                p["heading"],
                f"{speed:.2f}" if isinstance(speed, (int, float)) else speed,
                p["formatted_address"],
                p["http_request_time"],
            ])

    # Write JSONL
    with open(output_jsonl, "w", encoding="utf-8") as f:
        for p in points:
            f.write(json.dumps(p) + "\n")

    logger.info(f"Saved to {output_csv} and {output_jsonl}")

    # Print summary of time span and route bounds
    start_time = points[0]["iso_time"]
    end_time = points[-1]["iso_time"]
    lats = [p["latitude"] for p in points if p["latitude"] is not None]
    lons = [p["longitude"] for p in points if p["longitude"] is not None]

    print("\n" + "="*70)
    print("ROUTE SUMMARY FROM HAR EXPORT")
    print("="*70)
    print(f"Total Telemetry Points: {len(points)}")
    print(f"Device Name / ID:       {points[0]['device_name']} (ID: {points[0]['device_id']})")
    print(f"Time Window (UTC):      {start_time} --> {end_time}")
    print(f"Latitude Range:         {min(lats):.6f} to {max(lats):.6f}")
    print(f"Longitude Range:        {min(lons):.6f} to {max(lons):.6f}")
    print("="*70)
    print(f"\nFirst 3 points:")
    for p in points[:3]:
        print(f"  {p['iso_time']} | Lat: {p['latitude']:.6f}, Lon: {p['longitude']:.6f} | Speed: {p['speed_mph']:.1f} mph | Head: {p['heading']} deg | {p['formatted_address']}")
    print(f"\nLast 3 points:")
    for p in points[-3:]:
        print(f"  {p['iso_time']} | Lat: {p['latitude']:.6f}, Lon: {p['longitude']:.6f} | Speed: {p['speed_mph']:.1f} mph | Head: {p['heading']} deg | {p['formatted_address']}")
    print("="*70)

    return points


if __name__ == "__main__":
    har_file = sys.argv[1] if len(sys.argv) > 1 else "cloud.samsara.com.har"
    parse_har(har_file)
