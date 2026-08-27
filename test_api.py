"""
Test script for verifying Samsara Fleet Viewer GraphQL API.
"""

import json
import logging
import os
import sys

from samsara_client import SamsaraClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = os.getenv("SAMSARA_TOKEN", "")

def test_fetch():
    if not TOKEN:
        print("Please set SAMSARA_TOKEN environment variable or provide a token.")
        return

    client = SamsaraClient(token=TOKEN)
    logging.info(f"Initialized client for token: {TOKEN}")

    gql_resp = client.query_fleet_viewer(duration_ms=30000)
    logging.info(f"Status: {gql_resp.status_code}")
    print(json.dumps(gql_resp.json(), indent=2))

if __name__ == "__main__":
    test_fetch()
