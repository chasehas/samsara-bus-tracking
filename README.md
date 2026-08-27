# Samsara School Bus Geofence Alerts 🚌

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

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
- **Instant Mobile Push Alerts**: Delivers notifications via **[ntfy.sh](https://ntfy.sh)** with custom sounds, emojis, and click-through map links.
- **Cloud & Local Ready**: Run 24/7 in the cloud (Render, Fly.io, Docker) or locally on PowerShell/Linux.

---

## 🚀 Option 1: 1-Click Cloud Deployment (No Code / 24/7 Free)

Deploy to **Render** in under 2 minutes:

1. Install the free **ntfy** app on your phone ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Pick a private topic name (e.g., `myfamily-bus-alert-74921`) and subscribe in the app.
3. Click the button below:

   [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

4. Paste your configuration JSON into the `CONFIG_JSON` environment variable field in the Render web dashboard and click **Deploy**.

---

## 💻 Option 2: Local / Server Setup

### 1. Clone & Install
```bash
git clone https://github.com/your-username/samsara-bus-tracking.git
cd samsara-bus-tracking
pip install -r requirements.txt
```

### 2. Configure
Copy `config.example.json` to `config.json` and enter your coordinates and ntfy topic.

### 3. Run

**PowerShell / Terminal**:
```bash
python main.py --config config.json
```

**Docker**:
```bash
docker-compose up -d
```

---

## Testing & Simulation

```bash
# Test push alert
python notifier.py --topic your-topic-name

# Replay a recorded route against a profile
python simulate.py logs/route_sample.csv --profile "Morning Run"

# Find coordinates for an address
python lookup_coords.py "Main St & Oak Ave, Springfield, IL"
```

---

## License
MIT
