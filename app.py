"""
Flask app setup — thin orchestrator.

All route logic lives in routes/*.py blueprints.
State is held in AppState, stored on app.config["APP_STATE"].
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from flask import Flask, jsonify, render_template

from scanner import Scanner
from storage import SurveyStorage, Survey
from heatmap import RSSI_MIN, RSSI_MAX, SNR_MIN, SNR_MAX


# ── App State ────────────────────────────────────────────────────

@dataclass
class AppState:
    """Holds all mutable server state. No more globals."""
    survey_path: str
    floorplan_path: str
    snapshots_dir: str
    storage: SurveyStorage
    survey: Survey
    scanner: Optional[Scanner] = None


# ── Factory ──────────────────────────────────────────────────────

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

SURVEY_PATH = os.path.join(DATA_DIR, "survey.json")
FLOORPLAN_PATH = os.path.join(DATA_DIR, "floorplan.png")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

storage = SurveyStorage(SURVEY_PATH)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["VERSION"] = str(int(time.time()))

# Create and store state
app.config["APP_STATE"] = AppState(
    survey_path=SURVEY_PATH,
    floorplan_path=FLOORPLAN_PATH,
    snapshots_dir=SNAPSHOTS_DIR,
    storage=storage,
    survey=storage.load(),
)

# Register blueprints
from routes.survey import bp as survey_bp
from routes.snapshots import bp as snapshots_bp
from routes.scan import bp as scan_bp

app.register_blueprint(survey_bp)
app.register_blueprint(snapshots_bp)
app.register_blueprint(scan_bp)


# ── Remaining routes (index + status) ────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    st = app.config["APP_STATE"]
    size = None
    if st.survey.floorplan and os.path.exists(st.floorplan_path):
        from PIL import Image
        with Image.open(st.floorplan_path) as img:
            size = img.size
    return jsonify({
        "authenticated": st.scanner is not None,
        "has_floorplan": size is not None,
        "floorplan_width": size[0] if size else None,
        "floorplan_height": size[1] if size else None,
        "point_count": len(st.survey.points),
        "rssi_min": RSSI_MIN,
        "rssi_max": RSSI_MAX,
        "snr_min": SNR_MIN,
        "snr_max": SNR_MAX,
    })


# ── Entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  WiFi Heatmap")
    print("=" * 50)
    print(f"  Open http://localhost:5001 in your browser")
    print(f"  Survey data: {SURVEY_PATH}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=False)
