"""
Coordinate Lookup Helper
Converts a street address to latitude/longitude using OpenStreetMap Nominatim.
"""

import argparse
import sys
import requests


def lookup_address(address: str):
    headers = {"User-Agent": "BusTrackerAddressLookup/1.0"}
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 3,
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()

        if not results:
            print(f"\nNo coordinates found for: '{address}'")
            print("Tip: Try adding city, state, or zip code, or right-click your stop in Google Maps.")
            return

        print(f"\nFound {len(results)} match(es) for: '{address}':\n")
        for i, res in enumerate(results, 1):
            lat = float(res["lat"])
            lon = float(res["lon"])
            print(f"[{i}] {res['display_name']}")
            print(f"    Latitude:  {lat:.6f}")
            print(f"    Longitude: {lon:.6f}")
            print(f'    JSON: "stop_lat": {lat:.6f}, "stop_lon": {lon:.6f}\n')

    except Exception as e:
        print(f"Lookup error: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        query = input("Enter street address or intersection: ")
    else:
        query = " ".join(sys.argv[1:])

    lookup_address(query)
