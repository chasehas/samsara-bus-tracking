"""
Samsara Fleet Viewer Client
Handles CSRF authentication handshake and GraphQL telemetry queries.
"""

import logging
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

GRAPHQL_QUERY = """
query FleetViewer($token: string!, $duration: int64!) {
  fleetViewerToken(token: $token) {
    devices(feature: "fleetTrackable") {
      name
      id
      location: fleetViewerLocation(duration: $duration) {
        time
        latitude
        longitude
        heading
        speed
        formatted
      }
    }
  }
}
"""

class SamsaraClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://us7-ws.cloud.samsara.com",
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ):
        # Extract token if user passed a full URL (e.g. https://cloud.samsara.com/fleet/viewer/<TOKEN>)
        clean_token = token.strip()
        if "/fleet/viewer/" in clean_token:
            clean_token = clean_token.split("/fleet/viewer/")[-1].split("?")[0].strip("/")
        elif "/" in clean_token:
            clean_token = clean_token.rstrip("/").split("/")[-1]

        self.token = clean_token
        self.base_url = base_url.rstrip("/")
        self.csrf_url = f"{self.base_url}/r/auth/csrf"
        self.graphql_url = f"{self.base_url}/r/graphql?q=FleetViewer"
        self.user_agent = user_agent

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Origin": "https://cloud.samsara.com",
            "Referer": f"https://cloud.samsara.com/fleet/viewer/{self.token}",
        })
        self.csrf_token: Optional[str] = None

    def refresh_csrf(self) -> str:
        """Obtain a new CSRF token and session cookie."""
        logger.debug("Refreshing CSRF token from %s", self.csrf_url)
        resp = self.session.get(self.csrf_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self.csrf_token = data.get("csrf_token")
        if not self.csrf_token:
            raise ValueError(f"No csrf_token in response: {data}")
        logger.debug("Obtained CSRF token: %s...", self.csrf_token[:10])
        return self.csrf_token

    def get_latest_locations(self, duration_ms: int = 30000, retry_on_csrf: bool = True) -> List[Dict[str, Any]]:
        """
        Queries Samsara GraphQL endpoint for fleet tracker devices and their recent locations.
        """
        if not self.csrf_token:
            self.refresh_csrf()

        headers = {
            "x-csrf-token": self.csrf_token,
            "content-type": "application/json",
        }
        payload = {
            "query": GRAPHQL_QUERY,
            "variables": {
                "token": self.token,
                "duration": duration_ms,
            },
        }

        resp = self.session.post(self.graphql_url, json=payload, headers=headers, timeout=15)

        # Handle CSRF expiry / invalid token error
        if resp.status_code == 400 and "csrf" in resp.text.lower() and retry_on_csrf:
            logger.warning("CSRF token rejected. Refreshing CSRF and retrying query...")
            self.refresh_csrf()
            return self.get_latest_locations(duration_ms=duration_ms, retry_on_csrf=False)

        resp.raise_for_status()
        res_json = resp.json()

        if "errors" in res_json:
            errors = res_json.get("errors")
            # If error relates to CSRF, retry once
            if retry_on_csrf and any("csrf" in str(e).lower() for e in errors):
                logger.warning("GraphQL returned CSRF error. Refreshing and retrying...")
                self.refresh_csrf()
                return self.get_latest_locations(duration_ms=duration_ms, retry_on_csrf=False)
            raise RuntimeError(f"GraphQL errors: {errors}")

        devices = (
            res_json.get("output", {})
            .get("fleetViewerToken", {})
            .get("devices", [])
        )
        return devices
