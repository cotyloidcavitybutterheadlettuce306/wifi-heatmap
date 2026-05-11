# Contributing to wifi-heatmap

Thanks for your interest in contributing! This is a small personal project — contributions are welcome but keep them focused.

## Getting started

```bash
git clone https://github.com/ribaldorafael/wifi-heatmap.git
cd wifi-heatmap
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open http://localhost:5001 in your browser.

## Running tests

```bash
python3 -m unittest discover -s tests -v
```

There are 110+ tests across unit, integration, and insights tests. All tests must pass before submitting a PR.

## Project structure

```
app.py              Flask setup, AppState, blueprint registration
routes/
  survey.py         Points, APs, rooms, floorplan, heatmap
  snapshots.py      Snapshot CRUD, insights, comparison
  scan.py           Auth, measure, live scan, export
scanner.py          Scanner Protocol, WifiScanner, RemoteWifiScanner
storage.py          JSON persistence, data models
heatmap.py          IDW interpolation + matplotlib rendering
insights.py         Analysis engine (dead zones, coverage, quality)
export.py           PIL compositing for PNG export
agent.py            Standalone remote scan agent (cross-platform)
static/             JS + CSS (vanilla, no framework)
templates/          Single-page HTML
tests/              Unit + integration tests
```

## Guidelines

- **Keep it simple.** No build tools, no JS frameworks, no extra dependencies unless absolutely necessary.
- **Test your changes.** Add tests for new features. Run the full suite before submitting.
- **One feature per PR.** Small, focused PRs are easier to review.
- **Match the style.** Look at existing code. Dark theme, monospace data, Geist fonts.
- **macOS is the primary platform** for the scanner (uses `wdutil`). The agent supports macOS, Linux, and Windows.
- **Don't commit survey data.** The `data/` directory is gitignored for a reason.

## What to work on

Check the Issues tab for open items. Good first contributions:
- Bug fixes
- Test coverage improvements
- Documentation
- Cross-platform scanner improvements (Linux/Windows)
- UI polish

## Code of conduct

Be kind, be constructive, be respectful.
