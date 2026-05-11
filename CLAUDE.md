# wifi-heatmap — context for Claude Code

A WiFi coverage heatmap tool. Click points on a floor plan, walk around, visualize signal strength.

## Architecture

```
app.py              Flask setup, AppState class, blueprint registration
routes/
  survey.py         Points, APs, rooms, floorplan, heatmap routes
  snapshots.py      Snapshot CRUD, insights, comparison routes
  scan.py           Auth, measure, live scan, export routes
scanner.py          Scanner Protocol + implementations (local wdutil, remote agent)
storage.py          JSON load/save (atomic writes via .tmp + rename)
heatmap.py          numpy-only IDW interpolation + matplotlib PNG render
insights.py         Analysis engine (pure Python + numpy, no Flask dependency)
export.py           PIL compositing for PNG export
agent.py            Standalone remote scan agent (cross-platform, zero deps)
static/             JS + CSS (vanilla, no framework, Geist fonts, dark theme)
templates/          Single-page HTML
tests/              110+ unit + integration tests
data/               floorplan.png, survey.json, snapshots/ (gitignored)
```

## Key design decisions (already made — don't re-litigate unless asked)

- **macOS primary for local scanning.** `wdutil info` is the only reliable way to get RSSI/BSSID on modern macOS. Remote agent supports macOS, Linux, Windows.
- **Scanner Protocol.** `WifiScanner` and `RemoteWifiScanner` implement the `Scanner` Protocol (scan, verify_credentials, force_reconnect).
- **AppState class.** No global mutable state. All state (scanner, survey, storage) lives in `app.config["APP_STATE"]`.
- **Flask blueprints.** Routes split into survey, snapshots, scan modules.
- **No scipy.** numpy-only IDW (power=2). scipy lacks Python 3.14 arm64 wheels.
- **Single-page UI.** Vanilla JS, single `state` object, all DOM updates flow through `render*()` functions.
- **Insights are pure.** No Flask, no IO — data in, findings out. Accepts SurveyPoint objects or dicts.
- **Fuzzy BSSID matching.** First 14 chars of normalized MAC — handles mesh nodes with offset radio MACs.
- **Per measurement: 3 scans, strongest wins.** If BSSID changes between scans, last reading used (roaming completed).

## How to run

```bash
./run.sh   # creates venv on first run, then launches
# or: python3 app.py
```

Opens at http://localhost:5001.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Setup gotchas already solved

- **AdBlock breaks the page** — needs incognito or AdBlock disabled.
- **CSS [hidden] override** — global `[hidden] { display: none !important }` rule needed.
- **Python 3.14 + arm64** — current target. scipy unavailable, hence numpy-only heatmap.
- **Geist font** — loaded from Google Fonts. Offline use shows system fallback.
- **Browser caching** — `app.config["VERSION"]` uses timestamp for cache-busting on every restart.
