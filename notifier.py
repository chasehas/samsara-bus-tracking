"""
Notification Dispatcher (ntfy.sh)
Sends push notifications to mobile devices via ntfy.sh topics.
"""

import argparse
import logging
from typing import List, Optional
import requests

logger = logging.getLogger("notifier")


class NtfyNotifier:
    def __init__(
        self,
        topic: str,
        server_url: str = "https://ntfy.sh",
        default_title: str = "School Bus Alert 🚌",
        default_priority: str = "high",
        default_tags: Optional[List[str]] = None,
    ):
        self.topic = topic.strip()
        self.server_url = server_url.rstrip("/")
        self.default_title = default_title
        self.default_priority = default_priority
        self.default_tags = default_tags or ["bus", "warning"]

    def _map_priority(self, p: str) -> int:
        mapping = {
            "min": 1,
            "low": 2,
            "default": 3,
            "high": 4,
            "urgent": 5,
            "max": 5,
        }
        return mapping.get(p.lower(), 4)

    def send_alert(
        self,
        message: str,
        title: Optional[str] = None,
        priority: Optional[str] = None,
        tags: Optional[List[str]] = None,
        click_url: Optional[str] = None,
    ) -> bool:
        """
        Send a push notification to the configured ntfy topic via JSON POST.
        Supports full Unicode/emojis and clickable URLs.
        """
        payload = {
            "topic": self.topic,
            "message": message,
            "title": title or self.default_title,
            "priority": self._map_priority(priority or self.default_priority),
            "tags": tags if tags is not None else self.default_tags,
        }
        if click_url:
            payload["click"] = click_url

        try:
            logger.info("Sending ntfy alert to topic '%s'...", self.topic)
            resp = requests.post(
                self.server_url,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Notification successfully delivered to ntfy.sh!")
            return True
        except Exception as e:
            logger.error("Failed to send ntfy notification: %s", e)
            return False


def main():
    parser = argparse.ArgumentParser(description="Test ntfy.sh push notifications.")
    parser.add_argument("--topic", required=True, help="Your private ntfy.sh topic name")
    parser.add_argument("--message", default="Test alert: School bus is approaching!", help="Notification message")
    parser.add_argument("--title", default="School Bus Test 🚌", help="Notification title")
    parser.add_argument("--priority", default="high", choices=["min", "low", "default", "high", "urgent", "max"])
    parser.add_argument("--click", default=None, help="Click URL")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    notifier = NtfyNotifier(topic=args.topic)
    success = notifier.send_alert(
        message=args.message,
        title=args.title,
        priority=args.priority,
        click_url=args.click,
    )
    if success:
        print(f"\n[OK] Sent test message to https://ntfy.sh/{args.topic}")
        print("Tip: Subscribe to this topic in the free 'ntfy' iOS/Android app to receive push alerts.")
    else:
        print("\n[ERROR] Failed to send notification.")


if __name__ == "__main__":
    main()
