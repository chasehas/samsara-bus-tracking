# Samsara School Bus Geofence Alerts 🚌

A lightweight automation that restores real-time proximity alerts for school buses using public Samsara Fleet Viewer links.

Delivers instant push notifications to your mobile phone via **[ntfy.sh](https://ntfy.sh)** (free, zero subscriptions, works on iOS and Android) as the bus approaches your stop.

---

## Features

- **Direct Samsara Telemetry**: Queries Samsara's public GraphQL endpoint with automatic CSRF handshake and session refreshing.
- **Staged Multi-Tier Checkpoints**:
  - **1. Approach Alert**: Early warning when the bus passes an approach road (e.g. 3–4 mins away).
  - **2. Neighborhood Entrance**: Alerts when the bus enters your subdivision (e.g. 1–2 mins away).
  - **3. Street Entrance**: Urgent walk-outside alert when the bus turns onto your street.
- **Separate AM & PM Route Profiles**: Automatically applies different checkpoint sequences and time windows for morning pickup vs. afternoon dropoff.
- **Timezone-Aware Scheduling**: Evaluates active windows according to your local school timezone (`America/New_York`).
- **Vehicle Filtering**: Filter by bus name (`"target_device_name": "1710"`) to avoid conflicts when multiple buses share a viewer token.
- **Resilient & Persisted**: Confirms notification delivery before marking checkpoints fired; remembers state across restarts.
- **Instant Mobile Push Alerts**: Delivers notifications via **[ntfy.sh](https://ntfy.sh)** with custom sounds, emojis, and click-through map links.

---

## Quick Start (Local Run)

### 1. Prerequisites
- Python 3.10+ installed
- The free **ntfy** app installed on your phone ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy))

### 2. Clone & Install
```bash
git clone https://github.com/your-username/samsara-bus-tracking.git
cd samsara-bus-tracking
pip install -r requirements.txt
```

### 3. Configure
Copy `config.example.json` to `config.json`:
```bash
cp config.example.json config.json
```

Edit `config.json` with your:
1. `samsara_token` (the token from your district's Samsara viewer link: `https://cloud.samsara.com/fleet/viewer/<TOKEN>`)
2. `ntfy.topic` (your chosen private topic subscribed in the ntfy app)
3. Coordinates for your approach, subdivision, and street checkpoints.

### 4. Run

**PowerShell / Terminal**:
```bash
python main.py --config config.json
```

**Docker Compose**:
```bash
docker-compose up -d
```

---

## Configuration Reference

```json
{
  "samsara_token": "YOUR_SAMSARA_VIEWER_TOKEN_HERE",
  "timezone": "America/New_York",
  "target_device_name": "1710",
  "poll_interval_sec": 8.0,
  "query_duration_ms": 30000,
  "home_stop": {
    "lat": 37.774929,
    "lon": -122.419416
  },
  "profiles": [
    {
      "name": "Morning Run",
      "window": "06:45-08:30",
      "require_sequential": false,
      "cooldown_seconds": 7200.0,
      "triggers": [
        {
          "name": "1. Approach Alert",
          "lat": 37.764929,
          "lon": -122.419416,
          "radius_meters": 200.0,
          "min_speed_mph": 3.0,
          "title": "Morning Bus Approaching (3-4 mins) 🚌",
          "message": "Bus passed approach checkpoint (approx. 3-4 minutes to your stop).",
          "priority": "default",
          "tags": ["bus", "clock3"]
        },
        {
          "name": "2. Neighborhood Entrance",
          "lat": 37.771000,
          "lon": -122.418000,
          "radius_meters": 100.0,
          "min_speed_mph": 3.0,
          "title": "Morning Bus in Neighborhood (1-2 mins) 🚌",
          "message": "Bus entered neighborhood (approx. 1-2 minutes away).",
          "priority": "high",
          "tags": ["bus", "warning"]
        },
        {
          "name": "3. Street Entrance",
          "lat": 37.774000,
          "lon": -122.419000,
          "radius_meters": 85.0,
          "min_speed_mph": 3.0,
          "title": "Morning Bus on Street - Walk Outside! 🚨",
          "message": "Bus is turning onto your street! Time to walk outside.",
          "priority": "urgent",
          "tags": ["bus", "rotating_light"]
        }
      ]
    },
    {
      "name": "Afternoon Run",
      "window": "14:15-16:30",
      "require_sequential": false,
      "cooldown_seconds": 7200.0,
      "triggers": [
        {
          "name": "1. Neighborhood Entrance",
          "lat": 37.771000,
          "lon": -122.418000,
          "radius_meters": 100.0,
          "min_speed_mph": 3.0,
          "title": "Afternoon Bus in Neighborhood 🚌",
          "message": "Bus entered neighborhood (starting drop-off loop).",
          "priority": "default",
          "tags": ["bus", "clock5"]
        },
        {
          "name": "2. Street Entrance",
          "lat": 37.774000,
          "lon": -122.419000,
          "radius_meters": 85.0,
          "min_speed_mph": 3.0,
          "title": "Afternoon Bus on Street - Walk Outside! 🚨",
          "message": "Bus is turning onto your street! Time to walk outside.",
          "priority": "urgent",
          "tags": ["bus", "rotating_light"]
        }
      ]
    }
  ],
  "ntfy": {
    "topic": "your-private-ntfy-topic-here",
    "server_url": "https://ntfy.sh"
  }
}
```

---

## Testing & Simulation

```bash
# Run unit tests
pytest tests/ -v

# Test push notification delivery
python notifier.py --topic your-topic-name

# Replay a recorded route against a profile
python simulate.py logs/route_sample.csv --profile "Morning Run"

# Lookup coordinates for an address
python lookup_coords.py "Main St & Oak Ave, Springfield, IL"
```

---

## License
MIT
