"""Snapshot routes — save, list, get, restore, delete, insights."""
from __future__ import annotations

import json
import os
import re
import time

from flask import Blueprint, request, jsonify

from flask import send_file

from heatmap import rssi_to_color
from insights import compute_insights, compare_surveys
from storage import Survey, SurveyStorage
from export import render_export

bp = Blueprint("snapshots", __name__)


def _get_state():
    from flask import current_app
    return current_app.config["APP_STATE"]


def _floorplan_size():
    from routes.survey import _floorplan_size as fs
    return fs()


def _point_to_dict(p):
    from routes.survey import _point_to_dict as pd
    return pd(p)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:80] or "snapshot"


# ── Insights ─────────────────────────────────────────────────────

@bp.route("/api/insights")
def insights():
    st = _get_state()
    size = _floorplan_size()
    if size is None:
        return jsonify({"findings": []})
    w, h = size
    bssid_filter = request.args.get("bssid") or None
    ap_dicts = [a.to_dict() for a in st.survey.access_points]
    room_dicts = [r.to_dict() for r in st.survey.rooms]
    findings = compute_insights(st.survey.points, w, h, bssid_filter=bssid_filter,
                                access_points=ap_dicts, rooms=room_dicts)
    return jsonify({"findings": findings})


# ── Snapshots ────────────────────────────────────────────────────

@bp.route("/api/snapshots", methods=["POST"])
def save_snapshot():
    st = _get_state()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        name = f"Snapshot {time.strftime('%Y-%m-%d %H:%M')}"
    slug = _slugify(name)

    path = os.path.join(st.snapshots_dir, f"{slug}.json")
    if os.path.exists(path):
        slug = f"{slug}-{int(time.time())}"
        path = os.path.join(st.snapshots_dir, f"{slug}.json")

    snapshot_data = {
        "name": name,
        "slug": slug,
        "timestamp": time.time(),
        "survey": st.survey.to_dict(),
    }
    with open(path, "w") as f:
        json.dump(snapshot_data, f, indent=2)

    return jsonify({"ok": True, "slug": slug, "name": name})


@bp.route("/api/snapshots")
def list_snapshots():
    st = _get_state()
    snapshots = []
    for fname in sorted(os.listdir(st.snapshots_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(st.snapshots_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            snapshots.append({
                "slug": data.get("slug", fname[:-5]),
                "name": data.get("name", fname[:-5]),
                "timestamp": data.get("timestamp"),
                "point_count": len(data.get("survey", {}).get("points", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    snapshots.sort(key=lambda s: s.get("timestamp") or 0, reverse=True)
    return jsonify({"snapshots": snapshots})


@bp.route("/api/snapshots/<slug>")
def get_snapshot(slug):
    st = _get_state()
    path = os.path.join(st.snapshots_dir, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({"error": "Snapshot not found"}), 404
    with open(path) as f:
        data = json.load(f)

    size = _floorplan_size()
    snap_findings = []
    if size:
        snap_survey = data.get("survey", {})
        snap_points = []
        for p in snap_survey.get("points", []):
            d = dict(p)
            if d.get("sample", {}).get("rssi") is not None:
                d["color"] = rssi_to_color(d["sample"]["rssi"])
            else:
                d["color"] = "#999"
            snap_points.append(d)
        w, h = size
        snap_findings = compute_insights(snap_points, w, h)

        cur_points = [_point_to_dict(pt) for pt in st.survey.points]
        diff_findings = compare_surveys(cur_points, snap_points, w, h)
    else:
        diff_findings = []

    data["findings"] = snap_findings
    data["diff"] = diff_findings
    return jsonify(data)


@bp.route("/api/snapshots/<slug>/restore", methods=["POST"])
def restore_snapshot(slug):
    st = _get_state()
    path = os.path.join(st.snapshots_dir, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({"error": "Snapshot not found"}), 404
    with open(path) as f:
        data = json.load(f)
    st.survey = Survey.from_dict(data["survey"])
    st.storage.save(st.survey)
    return jsonify({"ok": True, "point_count": len(st.survey.points)})


@bp.route("/api/snapshots/<slug>/export")
def export_snapshot(slug):
    """Render the snapshot's data as an export PNG."""
    st = _get_state()
    path = os.path.join(st.snapshots_dir, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({"error": "Snapshot not found"}), 404
    if not os.path.exists(st.floorplan_path):
        return jsonify({"error": "No floor plan"}), 400

    with open(path) as f:
        data = json.load(f)

    snap_survey = Survey.from_dict(data["survey"])
    png_bytes = render_export(st.floorplan_path, snap_survey, show_heatmap=True, alpha=0.6)

    import io
    return send_file(io.BytesIO(png_bytes), mimetype="image/png", max_age=0)


@bp.route("/api/snapshots/<slug>", methods=["DELETE"])
def delete_snapshot(slug):
    st = _get_state()
    path = os.path.join(st.snapshots_dir, f"{slug}.json")
    if not os.path.exists(path):
        return jsonify({"error": "Snapshot not found"}), 404
    os.remove(path)
    return jsonify({"ok": True})
