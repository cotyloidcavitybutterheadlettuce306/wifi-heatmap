"""Survey routes — points, APs, rooms, floorplan, heatmap, bssids."""
from __future__ import annotations

import io
import os

from flask import Blueprint, request, jsonify, send_file
from PIL import Image

from heatmap import render_heatmap, HeatmapError, RSSI_MIN, RSSI_MAX, SNR_MIN, SNR_MAX
from heatmap import rssi_to_color
from storage import Survey

bp = Blueprint("survey", __name__)


def _get_state():
    from flask import current_app
    return current_app.config["APP_STATE"]


def _floorplan_size():
    st = _get_state()
    if not st.survey.floorplan or not os.path.exists(st.floorplan_path):
        return None
    with Image.open(st.floorplan_path) as img:
        return img.size


def _point_to_dict(p):
    d = p.to_dict()
    if p.sample.rssi is not None:
        d["color"] = rssi_to_color(p.sample.rssi)
    else:
        d["color"] = "#999"
    return d


# ── Floorplan ────────────────────────────────────────────────────

@bp.route("/api/floorplan", methods=["POST"])
def upload_floorplan():
    st = _get_state()
    if "file" not in request.files:
        return jsonify({"error": "No file in upload"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    try:
        img = Image.open(file.stream)
        img.load()
    except Exception as e:
        return jsonify({"error": f"Not a valid image: {e}"}), 400

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img.save(st.floorplan_path, format="PNG")

    st.survey = Survey(floorplan="floorplan.png")
    st.storage.save(st.survey)

    return jsonify({"ok": True, "width": img.size[0], "height": img.size[1]})


@bp.route("/api/floorplan")
def get_floorplan():
    st = _get_state()
    if not os.path.exists(st.floorplan_path):
        return jsonify({"error": "No floor plan uploaded"}), 404
    return send_file(st.floorplan_path, mimetype="image/png")


# ── Points ───────────────────────────────────────────────────────

@bp.route("/api/points")
def list_points():
    st = _get_state()
    return jsonify({"points": [_point_to_dict(p) for p in st.survey.points]})


@bp.route("/api/points/<point_id>", methods=["DELETE"])
def delete_point(point_id):
    st = _get_state()
    if not st.survey.remove_point(point_id):
        return jsonify({"error": "Point not found"}), 404
    st.storage.save(st.survey)
    return jsonify({"ok": True})


@bp.route("/api/points/<point_id>/note", methods=["POST"])
def update_note(point_id):
    st = _get_state()
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()
    for p in st.survey.points:
        if p.id == point_id:
            p.note = note
            st.storage.save(st.survey)
            return jsonify({"ok": True, "note": note})
    return jsonify({"error": "Point not found"}), 404


@bp.route("/api/clear", methods=["POST"])
def clear_points():
    st = _get_state()
    st.survey.clear_points()
    st.storage.save(st.survey)
    return jsonify({"ok": True})


@bp.route("/api/new-survey", methods=["POST"])
def new_survey():
    st = _get_state()
    st.survey = Survey(floorplan=st.survey.floorplan)
    st.storage.save(st.survey)
    return jsonify({"ok": True})


# ── Access Points ────────────────────────────────────────────────

@bp.route("/api/access_points")
def list_access_points():
    st = _get_state()
    return jsonify({"access_points": [a.to_dict() for a in st.survey.access_points]})


@bp.route("/api/access_points", methods=["POST"])
def add_access_point():
    st = _get_state()
    data = request.get_json(silent=True) or {}
    try:
        x = int(data["x"])
        y = int(data["y"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Need integer x and y"}), 400

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    size = _floorplan_size()
    if size is None:
        return jsonify({"error": "Upload a floor plan first"}), 400
    w, h = size
    if not (0 <= x <= w and 0 <= y <= h):
        return jsonify({"error": "Coords out of bounds"}), 400

    bssid = (data.get("bssid") or "").strip()
    ap = st.survey.add_access_point(x, y, name, bssid)
    st.storage.save(st.survey)
    return jsonify({"access_point": ap.to_dict()})


@bp.route("/api/access_points/<ap_id>", methods=["PATCH"])
def update_access_point(ap_id):
    st = _get_state()
    data = request.get_json(silent=True) or {}
    for ap in st.survey.access_points:
        if ap.id == ap_id:
            if "name" in data:
                ap.name = (data["name"] or "").strip() or ap.name
            if "bssid" in data:
                ap.bssid = (data["bssid"] or "").strip()
            st.storage.save(st.survey)
            return jsonify({"access_point": ap.to_dict()})
    return jsonify({"error": "Access point not found"}), 404


@bp.route("/api/access_points/<ap_id>", methods=["DELETE"])
def delete_access_point(ap_id):
    st = _get_state()
    if not st.survey.remove_access_point(ap_id):
        return jsonify({"error": "Access point not found"}), 404
    st.storage.save(st.survey)
    return jsonify({"ok": True})


@bp.route("/api/bssids")
def bssids():
    st = _get_state()
    return jsonify({"bssids": st.survey.get_unique_bssids()})


# ── Rooms ────────────────────────────────────────────────────────

@bp.route("/api/rooms")
def list_rooms():
    st = _get_state()
    return jsonify({"rooms": [r.to_dict() for r in st.survey.rooms]})


@bp.route("/api/rooms", methods=["POST"])
def add_room():
    st = _get_state()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    try:
        x1, y1 = int(data["x1"]), int(data["y1"])
        x2, y2 = int(data["x2"]), int(data["y2"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Need x1, y1, x2, y2"}), 400

    room = st.survey.add_room(name, x1, y1, x2, y2)
    st.storage.save(st.survey)
    return jsonify({"room": room.to_dict()})


@bp.route("/api/rooms/<room_id>", methods=["DELETE"])
def delete_room(room_id):
    st = _get_state()
    if not st.survey.remove_room(room_id):
        return jsonify({"error": "Room not found"}), 404
    st.storage.save(st.survey)
    return jsonify({"ok": True})


# ── Heatmap ──────────────────────────────────────────────────────

@bp.route("/api/heatmap")
def heatmap():
    st = _get_state()
    size = _floorplan_size()
    if size is None:
        return jsonify({"error": "No floor plan"}), 400
    w, h = size

    bssid_filter = request.args.get("bssid")
    try:
        alpha = float(request.args.get("alpha", 0.6))
        alpha = max(0.0, min(1.0, alpha))
    except ValueError:
        alpha = 0.6

    mode = request.args.get("mode", "rssi")

    candidates = [
        p for p in st.survey.points
        if p.sample.rssi is not None and (
            bssid_filter is None or p.sample.bssid == bssid_filter
        )
    ]

    if mode == "snr":
        triples = [(p.x, p.y, p.sample.rssi - (p.sample.noise or -90))
                    for p in candidates]
        hm_vmin, hm_vmax = SNR_MIN, SNR_MAX
    elif mode == "speed":
        triples = [(p.x, p.y, p.download_mbps)
                    for p in candidates if p.download_mbps is not None]
        hm_vmin, hm_vmax = 0, 200
    elif mode == "txrate":
        triples = [(p.x, p.y, p.sample.tx_rate)
                    for p in candidates if p.sample.tx_rate is not None]
        hm_vmin, hm_vmax = 0, 866
    else:
        triples = [(p.x, p.y, p.sample.rssi) for p in candidates]
        hm_vmin, hm_vmax = RSSI_MIN, RSSI_MAX

    try:
        png_bytes = render_heatmap(triples, w, h, alpha=alpha,
                                   vmin=hm_vmin, vmax=hm_vmax)
    except HeatmapError as e:
        return jsonify({"error": str(e)}), 400

    return send_file(io.BytesIO(png_bytes), mimetype="image/png", max_age=0)
