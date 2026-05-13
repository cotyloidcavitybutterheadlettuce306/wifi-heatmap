# wifi-heatmap

![Tests](https://img.shields.io/badge/tests-110%2B%20passing-brightgreen) ![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

A WiFi coverage heatmap tool. Click points on a floor plan, walk around your house, and visualize signal strength, throughput, and coverage gaps.

Built with Python + Flask. No cloud, no accounts — everything runs locally.

<img width="1703" height="873" alt="wifi-heatmap screenshot" src="https://github.com/user-attachments/assets/ddcf6923-a343-4d0c-9fa0-1f96d72d286e" />

## Features

- **Click-to-measure** on any floor plan image — each click captures RSSI, BSSID, channel, PHY mode, Tx rate, noise floor
- **Multi-scan averaging** — takes 3 samples per measurement, picks the strongest (or detects mesh roaming)
- **4 heatmap modes** — RSSI, SNR, Tx Rate, Download Speed
- **Room labels** — draw room boundaries, get per-room signal analysis
- **Access point mapping** — mark where your routers are, link by BSSID (fuzzy MAC matching for mesh nodes)
- **Insights engine** — dead zones, weak points, AP coverage %, sticky-client detection, survey quality scoring
- **Snapshots & comparison** — save a survey, move your router, re-survey, compare with real numbers
- **Live signal monitor** — real-time RSSI, SNR, channel, Tx rate dashboard
- **Speed test** — optional Cloudflare download test at each point
- **Force roam** — WiFi off/on toggle for mesh networks with sticky clients
- **PNG export** — composited floor plan + heatmap + markers + legend
- **Remote scanning** — run `agent.py` on another machine, control the survey from your browser

## Requirements

- **macOS 14+** for local scanning (uses `wdutil info`)
- **Python 3.9+**
- Connected to the WiFi network you want to map
- For remote scanning: `agent.py` runs on macOS, Linux, or Windows (zero dependencies)

## Install

```bash
git clone https://github.com/ribaldorafael/wifi-heatmap.git
cd wifi-heatmap
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
./run.sh
# or: python3 app.py
```

Open http://localhost:5001 in your browser.

## How to survey your home

### Step 1: Set up

```bash
./run.sh                    # start the server
open http://localhost:5001  # open in browser
```

Enter your sudo password on the auth screen (Local tab).

### Step 2: Prepare the floor plan

1. **Upload a floor plan** — any image works (PNG, JPG, photo of a paper blueprint, hand-drawn sketch)
2. **Draw rooms** — click "Draw Room" in the Points tab, click two corners on the map, name the room. Repeat for each room.
3. **Place access points** — click "Place AP", click where your router/mesh node is on the map, name it, and enter its BSSID (found in your router's admin app). The BSSID links measurements to specific nodes.

### Step 3: Walk and measure

1. Enable **Force roam** if you have a mesh network (Deco, Eero, Orbi) — this toggles WiFi off/on before each scan so your laptop connects to the nearest node instead of staying stuck on one
2. Optionally enable **Speed test** to capture download throughput at each point (adds ~2-5s per measurement, disable VPN first for accurate results)
3. **Walk to a spot**, click where you're standing on the floor plan, wait for the beep (~3-10s depending on toggles)
4. Repeat across your home — aim for at least 15-20 points spread across all rooms

### Step 4: Analyze

- Switch the **Metric** dropdown between RSSI, SNR, Tx Rate, or Speed to see different views
- Check the **Insights** tab for automated findings: dead zones, weak points, per-room averages, sticky-client warnings
- Use **Filter by AP** to see one mesh node's coverage area
- Click any marker or sidebar row to see detailed info

### Step 5: Optimize and compare

1. Click **Save Snapshot** to save the current state
2. Move a router, add a mesh node, or change settings
3. Re-survey the same spots
4. Use **Compare** to see side-by-side heatmaps and numerical diffs ("average RSSI improved by 3.2 dBm", "dead zone area reduced from 12% to 4%")

## Local vs Remote mode

Two ways to scan — choose on the auth screen:

```
LOCAL MODE (default)

  You carry the laptop and click on its own screen.

  ┌──────────────────────────────────────────┐
  │  Laptop (walks around)                   │
  │                                          │
  │  Browser ←→ Flask app ←→ wdutil (scan)   │
  │     ↑                                    │
  │  you click here                          │
  └──────────────────────────────────────────┘


REMOTE MODE

  One machine scans, another controls. Useful when you have a
  second machine but can't install Python packages on it.

  ┌─────────────────────────────┐     ┌──────────────────────────┐
  │  Your desk                  │     │  Remote machine           │
  │                             │     │  (walks around)           │
  │  Browser ←→ Flask app ──────────→ │  agent.py :5555           │
  │     ↑                      │     │  ↓                        │
  │  you click here            │     │  wdutil / nmcli / netsh   │
  └─────────────────────────────┘     └──────────────────────────┘

  1. Copy agent.py to the remote machine
  2. Run: python3 agent.py  (no pip install needed — pure stdlib)
  3. On the auth screen, switch to "Remote" tab, enter the IP
  4. Walk around with the remote machine, click positions from your desk
```

The remote agent auto-detects the OS:
- **macOS**: uses `wdutil info` (needs sudo)
- **Linux**: uses `nmcli` (no root) or `iwconfig` (needs root)
- **Windows**: uses `netsh wlan show interfaces` (no admin needed)

## Tips

- **Turn off your VPN** before surveying with speed test enabled — VPN tunnels bottleneck throughput and give misleading numbers
- **Enable Force roam** for mesh networks — without it, your laptop may "stick" to one node even when a closer one is available, making the heatmap look like you only have one AP
- **Link APs by BSSID** — the app uses fuzzy MAC matching (matches the first 5.5 octets) so it handles radio offsets automatically (e.g. your Deco's base MAC `:78` matches its 5 GHz radio `:7b`)
- **15+ measurements** unlocks dead zone analysis in the Insights tab
- **Draw rooms first** — insights will reference rooms by name ("Kitchen averages -68 dBm") instead of pixel coordinates
- **Save snapshots before changes** — you can always restore a snapshot if you want to go back

## Architecture

```
app.py              Flask setup, AppState, blueprint registration
routes/
  survey.py         Points, APs, rooms, floorplan, heatmap
  snapshots.py      Snapshot CRUD, insights, comparison
  scan.py           Auth, measure, live scan, export
scanner.py          Scanner Protocol, WifiScanner, RemoteWifiScanner
storage.py          JSON persistence (atomic writes)
heatmap.py          numpy-only IDW interpolation + matplotlib rendering
insights.py         Analysis engine (pure Python, no Flask dependency)
export.py           PIL compositing for PNG export
agent.py            Standalone remote scan agent (cross-platform, zero deps)
static/             JS + CSS (vanilla, no framework)
templates/          Single-page HTML (Geist font, dark theme)
tests/              110+ unit + integration tests
data/               Survey data (gitignored, created on first use)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Notes on macOS WiFi APIs

Modern macOS has progressively locked down WiFi data access:

- `airport -s` — **removed** in macOS 14.4
- `system_profiler SPAirPortDataType` — **redacts BSSIDs**
- `ioreg` for `IO80211BSSID` — returns **all zeros**
- `wdutil info` — **works fully** but requires sudo

We use `wdutil info`. The remote agent also supports Linux (`nmcli`/`iwconfig`) and Windows (`netsh`).

## License

MIT
