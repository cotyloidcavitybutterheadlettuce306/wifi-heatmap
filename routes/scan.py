"""Scan routes — auth, measure, live scan, export."""
from __future__ import annotations

import io
import time

from flask import Blueprint, request, jsonify, send_file, abort

from scanner import WifiScanner, RemoteWifiScanner, ScanError, speed_test
from heatmap import rssi_to_color
from export import render_export

bp = Blueprint("scan", __name__)


def _get_state():
    from flask import current_app
    return current_app.config["APP_STATE"]


def _require_auth():
    st = _get_state()
    if st.scanner is None:
        abort(401, description="Not authenticated. POST /api/auth first.")


def _point_to_dict(p):
    from routes.survey import _point_to_dict as pd
    return pd(p)


def _floorplan_size():
    from routes.survey import _floorplan_size as fs
    return fs()


# ── Auth ─────────────────────────────────────────────────────────

@bp.route("/api/auth", methods=["POST"])
def auth():
    st = _get_state()
    data = request.get_json(silent=True) or {}

    agent_url = (data.get("agent_url") or "").strip()
    if agent_url:
        try_scanner = RemoteWifiScanner(agent_url=agent_url)
        try:
            try_scanner.verify_credentials()
        except ScanError as e:
            return jsonify({"error": str(e)}), 401
        st.scanner = try_scanner
        return jsonify({"ok": True, "mode": "remote"})

    password = data.get("password", "")
    if not password:
        return jsonify({"error": "Password or agent URL required"}), 400

    try_scanner = WifiScanner(sudo_password=password)
    try:
        try_scanner.verify_credentials()
    except ScanError as e:
        return jsonify({"error": str(e)}), 401

    st.scanner = try_scanner
    return jsonify({"ok": True, "mode": "local"})


# ── Measure ──────────────────────────────────────────────────────

@bp.route("/api/measure", methods=["POST"])
def measure():
    _require_auth()
    st = _get_state()
    data = request.get_json(silent=True) or {}

    try:
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Need integer x and y"}), 400

    size = _floorplan_size()
    if size is None:
        return jsonify({"error": "Upload a floor plan first"}), 400
    w, h = size
    if not (0 <= x <= w and 0 <= y <= h):
        return jsonify({"error": "Coords out of bounds"}), 400

    # Force reconnect if requested
    force_roam = data.get("force_roam", False)
    if force_roam:
        try:
            st.scanner.force_reconnect()
        except ScanError:
            pass

    # Take 3 scans, 1 second apart
    samples = []
    for i in range(3):
        if i > 0:
            time.sleep(1)
        try:
            s = st.scanner.scan()
            if s.is_valid:
                samples.append(s)
        except ScanError:
            pass

    if not samples:
        return jsonify({"error": "All scans failed - is WiFi connected?"}), 500

    bssids_seen = set(s.bssid for s in samples if s.bssid)
    bssid_changed = len(bssids_seen) > 1

    if bssid_changed:
        best = samples[-1]
    else:
        best = max(samples, key=lambda s: s.rssi if s.rssi is not None else -999)

    # Optional speed test
    dl_mbps = None
    if data.get("speed_test", False):
        try:
            dl_mbps = speed_test()
        except Exception:
            pass

    all_samples = [s.to_dict() for s in samples]
    point = st.survey.add_point(x, y, best,
                                all_samples=all_samples,
                                bssid_changed=bssid_changed,
                                download_mbps=dl_mbps)
    st.storage.save(st.survey)
    return jsonify({"point": _point_to_dict(point)})


# ── Live scan ────────────────────────────────────────────────────

@bp.route("/api/scan")
def live_scan():
    _require_auth()
    st = _get_state()
    try:
        sample = st.scanner.scan()
    except ScanError as e:
        return jsonify({"error": str(e)}), 500
    d = sample.to_dict()
    if sample.rssi is not None:
        d["color"] = rssi_to_color(sample.rssi)
    return jsonify(d)


# ── Export ────────────────────────────────────────────────────────

@bp.route("/api/export")
def export():
    st = _get_state()
    size = _floorplan_size()
    if size is None:
        return jsonify({"error": "No floor plan"}), 400

    bssid_filter = request.args.get("bssid") or None
    show_heatmap = request.args.get("heatmap", "false") == "true"
    try:
        alpha = float(request.args.get("alpha", 0.6))
        alpha = max(0.0, min(1.0, alpha))
    except ValueError:
        alpha = 0.6

    png_bytes = render_export(
        st.floorplan_path, st.survey,
        bssid_filter=bssid_filter,
        show_heatmap=show_heatmap,
        alpha=alpha,
    )

    ts = time.strftime("%Y%m%d-%H%M%S")
    filename = f"wifi-survey-{ts}.png"
    return send_file(io.BytesIO(png_bytes), mimetype="image/png",
                     as_attachment=True, download_name=filename, max_age=0)
