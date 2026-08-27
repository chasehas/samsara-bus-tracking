"""
Unit Tests for School Bus Tracker & Geofence Engine
"""

from datetime import datetime, time as dtime, timezone
import json
import pytest
import time
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from geofence import (
    TriggerCheckpoint,
    RouteProfile,
    haversine_distance,
    parse_time_str,
)
import main
from main import (
    BusAlertService,
    HealthCheckHandler,
    format_speed,
    format_heading,
    load_config,
)


def test_haversine_distance():
    sf = (37.7749, -122.4194)
    sj = (37.3382, -121.8863)
    dist = haversine_distance(sf[0], sf[1], sj[0], sj[1])
    assert 65000 < dist < 75000


def test_trigger_checkpoint_evaluation_and_decoupled_firing():
    target_lat, target_lon = 33.5000, -82.2400
    cp = TriggerCheckpoint(
        name="Test Checkpoint",
        lat=target_lat,
        lon=target_lon,
        radius_meters=100.0,
        min_speed_mph=3.0,
    )

    # 1. Point far away (> 100m) -> does not trigger
    should_fire, dist = cp.evaluate(33.5100, -82.2400, timestamp_sec=1000.0, speed_mph=15.0)
    assert not should_fire
    assert dist > 100.0
    assert not cp.fired

    # 2. Point in range, but speed < 3.0 mph (e.g. stationary drift) -> does not trigger
    should_fire, dist = cp.evaluate(target_lat, target_lon, timestamp_sec=1001.0, speed_mph=1.5)
    assert not should_fire
    assert dist < 5.0
    assert not cp.fired

    # 3. Point in range, speed is None -> does not crash, does not trigger
    should_fire, dist = cp.evaluate(target_lat, target_lon, timestamp_sec=1002.0, speed_mph=None)
    assert not should_fire
    assert not cp.fired

    # 4. Point in range, speed >= 3.0 mph -> returns should_fire=True BUT does NOT set cp.fired until mark_fired()
    should_fire, dist = cp.evaluate(target_lat, target_lon, timestamp_sec=1003.0, speed_mph=12.0)
    assert should_fire
    assert not cp.fired

    # 5. Confirm mark_fired
    cp.mark_fired(timestamp_sec=1003.0)
    assert cp.fired
    assert cp.fired_time == 1003.0

    # 6. Once fired, evaluate returns False
    should_fire_again, _ = cp.evaluate(target_lat, target_lon, timestamp_sec=1004.0, speed_mph=12.0)
    assert not should_fire_again


def test_route_profile_timezone_evaluation():
    profile = RouteProfile(
        name="Morning Run",
        window_start=dtime(6, 45),
        window_end=dtime(8, 30),
        timezone="America/New_York",
    )

    tz_ny = ZoneInfo("America/New_York")
    
    active_dt = datetime(2026, 8, 27, 7, 15, 0, tzinfo=tz_ny)
    assert profile.is_time_active(active_dt) is True

    inactive_dt = datetime(2026, 8, 27, 11, 15, 0, tzinfo=tz_ny)
    assert profile.is_time_active(inactive_dt) is False


def test_route_profile_sequential_gating():
    cp1 = TriggerCheckpoint("Stage 1", lat=33.5000, lon=-82.2400, radius_meters=100.0, min_speed_mph=0.0)
    cp2 = TriggerCheckpoint("Stage 2", lat=33.5050, lon=-82.2400, radius_meters=100.0, min_speed_mph=0.0)
    profile = RouteProfile("Sequential Test", triggers=[cp1, cp2], require_sequential=True)

    fired, status = profile.evaluate_all_triggers(lat=33.5050, lon=-82.2400, speed=10.0)
    assert len(fired) == 0

    fired, status = profile.evaluate_all_triggers(lat=33.5000, lon=-82.2400, speed=10.0)
    assert len(fired) == 1
    assert fired[0][0].name == "Stage 1"
    cp1.mark_fired()

    fired, status = profile.evaluate_all_triggers(lat=33.5050, lon=-82.2400, speed=10.0)
    assert len(fired) == 1
    assert fired[0][0].name == "Stage 2"


def test_state_export_and_import():
    cp1 = TriggerCheckpoint("Stage 1", lat=33.5000, lon=-82.2400)
    cp2 = TriggerCheckpoint("Stage 2", lat=33.5050, lon=-82.2400)
    profile = RouteProfile("AM Run", triggers=[cp1, cp2], cooldown_seconds=7200.0)

    now_t = 100000.0
    cp1.mark_fired(timestamp_sec=now_t)

    state = profile.export_state()
    assert state == {"Stage 1": now_t}

    # New profile instance restoring state
    new_cp1 = TriggerCheckpoint("Stage 1", lat=33.5000, lon=-82.2400)
    new_cp2 = TriggerCheckpoint("Stage 2", lat=33.5050, lon=-82.2400)
    new_profile = RouteProfile("AM Run", triggers=[new_cp1, new_cp2], cooldown_seconds=7200.0)

    # 1. Restore within cooldown (e.g. 5 minutes later)
    new_profile.import_state(state, now_ts=now_t + 300)
    assert new_cp1.fired is True
    assert new_cp1.fired_time == now_t
    assert new_cp2.fired is False

    # 2. If restore happens after cooldown (e.g. 3 hours later), it stays unfired
    new_cp1.reset()
    new_profile.import_state(state, now_ts=now_t + 10800)
    assert new_cp1.fired is False


def test_formatting_helpers():
    assert format_speed(15.456) == "15.5mph"
    assert format_speed(None) == "N/A"
    assert format_heading(180) == "180°"
    assert format_heading(None) == "N/A"


def test_device_filtering_in_service():
    config = {
        "samsara_token": "dummy-token",
        "target_device_name": "1710",
        "profiles": [
            {
                "name": "Test Run",
                "triggers": [
                    {"name": "Stop", "lat": 33.5000, "lon": -82.2400, "radius_meters": 100.0, "min_speed_mph": 0.0}
                ]
            }
        ],
        "ntfy": {"topic": "test-topic"}
    }
    service = BusAlertService(config)
    service.client = MagicMock()
    service.client.token = "dummy-token"
    
    service.client.get_latest_locations.return_value = [
        {
            "id": "1",
            "name": "9999",
            "location": [{"latitude": 33.5000, "longitude": -82.2400, "speed": 10.0, "time": 1000}]
        },
        {
            "id": "2",
            "name": "1710",
            "location": [{"latitude": 33.6000, "longitude": -82.2400, "speed": 10.0, "time": 1000}]
        }
    ]

    service.notifier = MagicMock()
    service.notifier.send_alert.return_value = True

    service.tick()

    assert service.notifier.send_alert.call_count == 0


def test_delivery_retry_behavior():
    config = {
        "samsara_token": "dummy-token",
        "profiles": [
            {
                "name": "Test Run",
                "triggers": [
                    {"name": "Stop", "lat": 33.5000, "lon": -82.2400, "radius_meters": 100.0, "min_speed_mph": 0.0}
                ]
            }
        ],
        "ntfy": {"topic": "test-topic"}
    }
    service = BusAlertService(config)
    service.client = MagicMock()
    service.client.token = "dummy-token"
    service.client.get_latest_locations.return_value = [
        {
            "id": "1",
            "name": "1710",
            "location": [{"latitude": 33.5000, "longitude": -82.2400, "speed": 10.0, "time": 1000}]
        }
    ]

    service.notifier = MagicMock()
    service.notifier.send_alert.return_value = False

    service.tick()
    assert service.profiles[0].triggers[0].fired is False

    service.notifier.send_alert.return_value = True
    service.tick()
    assert service.profiles[0].triggers[0].fired is True


def test_config_loader_env_var(monkeypatch):
    dummy_payload = json.dumps({"samsara_token": "env-token-123", "profiles": []})
    monkeypatch.setenv("CONFIG_JSON", dummy_payload)
    cfg = load_config()
    assert cfg["samsara_token"] == "env-token-123"
