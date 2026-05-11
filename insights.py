"""
Insights engine — analyzes a WiFi survey and returns structured findings.

Pure Python + numpy. No LLM, no API calls, no Flask dependency.
Importable by app.py and fully unit-testable.

Each finding is a dict:
    severity:  "info" | "warn" | "bad"
    category:  short tag (e.g. "dead-zone", "coverage", "quality")
    title:     human heading
    body:      1-3 plain-English sentences (descriptive, not prescriptive)
    metric:    optional numeric value for display (None if absent)
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from heatmap import compute_rssi_grid, HeatmapError
from storage import SurveyPoint

# ── RSSI thresholds ──────────────────────────────────────────────
EXCELLENT = -50
GOOD = -60
USABLE = -70
POOR = -75
# < POOR  is "dead"

# Dead-zone grid threshold: -70 for warn, -75 for bad
DEAD_ZONE_WARN = -70
DEAD_ZONE_BAD = -75

# Tx rate below this is flagged
TX_RATE_LOW = 100  # Mbps

# Minimum points before we attempt area-based analysis
MIN_POINTS_AREA = 15
MIN_POINTS_BASIC = 3

# 4x4 grid for survey-quality check
QUALITY_GRID = 4

QUADRANT_NAMES = [
    ["top-left", "top-center-left", "top-center-right", "top-right"],
    ["upper-left", "upper-center-left", "upper-center-right", "upper-right"],
    ["lower-left", "lower-center-left", "lower-center-right", "lower-right"],
    ["bottom-left", "bottom-center-left", "bottom-center-right", "bottom-right"],
]


def rssi_label(rssi: int) -> str:
    if rssi >= EXCELLENT:
        return "excellent"
    if rssi >= GOOD:
        return "good"
    if rssi >= USABLE:
        return "usable"
    if rssi >= POOR:
        return "poor"
    return "dead"


def _bssid_matches(a: str, b: str) -> bool:
    """Fuzzy BSSID match — same device, different radio offset."""
    if not a or not b:
        return False
    a = a.lower().replace("-", ":")
    b = b.lower().replace("-", ":")
    if a == b:
        return True
    return len(a) >= 14 and len(b) >= 14 and a[:14] == b[:14]


def _ap_name_for_bssid(bssid, access_points):
    """Return AP name if BSSID fuzzy-matches a placed AP."""
    if not bssid:
        return None
    for ap in access_points:
        if _bssid_matches(bssid, ap.get("bssid", "")):
            return ap.get("name")
    return None


def _room_for_point(p, rooms):
    """Return room name if point falls inside a room rectangle, else None."""
    x, y = p["x"], p["y"]
    for r in rooms:
        if r["x1"] <= x <= r["x2"] and r["y1"] <= y <= r["y2"]:
            return r["name"]
    return None


def _location_label(p, rooms):
    """Return 'in the Kitchen' or 'at (x, y)' depending on room data."""
    room = _room_for_point(p, rooms)
    if room:
        return f"in the {room}"
    return f"at ({p['x']}, {p['y']})"


# ── Public API ───────────────────────────────────────────────────

def _to_dict(p) -> dict:
    """Accept either a SurveyPoint or a dict, return a dict."""
    if isinstance(p, SurveyPoint):
        return p.to_dict()
    return p


def compute_insights(
    points: list,
    width: int,
    height: int,
    bssid_filter: Optional[str] = None,
    access_points: Optional[list] = None,
    rooms: Optional[list] = None,
) -> List[dict]:
    point_dicts = [_to_dict(p) for p in points]

    if bssid_filter:
        filtered = [p for p in point_dicts if p.get("sample", {}).get("bssid") == bssid_filter]
    else:
        filtered = list(point_dicts)

    valid = [p for p in filtered if p.get("sample", {}).get("rssi") is not None]
    rooms = rooms or []
    access_points = access_points or []

    findings: List[dict] = []

    if len(valid) < MIN_POINTS_BASIC:
        return findings

    findings.extend(_analyze_extremes(valid, rooms, access_points))
    findings.extend(_analyze_weak_points(valid, rooms))
    findings.extend(_analyze_low_tx_rate(valid, rooms))
    findings.extend(_analyze_ap_coverage(valid))
    findings.extend(_analyze_sticky_client(valid, access_points))
    findings.extend(_analyze_survey_quality(valid, width, height))
    findings.extend(_analyze_dead_zones(valid, width, height))
    findings.extend(_analyze_rooms(valid, rooms))

    severity_order = {"bad": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 9))
    return findings


def compare_surveys(
    current_points: list,
    snapshot_points: list,
    width: int,
    height: int,
) -> List[dict]:
    findings: List[dict] = []

    cur_valid = [p for p in current_points if p.get("sample", {}).get("rssi") is not None]
    snap_valid = [p for p in snapshot_points if p.get("sample", {}).get("rssi") is not None]

    diff = len(cur_valid) - len(snap_valid)
    if diff != 0:
        word = "more" if diff > 0 else "fewer"
        findings.append({
            "severity": "info",
            "category": "comparison",
            "title": "Measurement count",
            "body": f"Current survey has {abs(diff)} {word} measurement{'s' if abs(diff) != 1 else ''} than the snapshot ({len(cur_valid)} vs {len(snap_valid)}).",
            "metric": diff,
        })

    if cur_valid and snap_valid:
        cur_avg = sum(p["sample"]["rssi"] for p in cur_valid) / len(cur_valid)
        snap_avg = sum(p["sample"]["rssi"] for p in snap_valid) / len(snap_valid)
        delta = round(cur_avg - snap_avg, 1)
        if abs(delta) >= 0.5:
            direction = "improved" if delta > 0 else "decreased"
            sev = "info" if delta >= 0 else "warn"
            findings.append({
                "severity": sev,
                "category": "comparison",
                "title": "Average signal change",
                "body": f"Average RSSI {direction} by {abs(delta)} dBm (now {round(cur_avg)} dBm, was {round(snap_avg)} dBm).",
                "metric": delta,
            })

    cur_dead_pct = _dead_zone_pct(cur_valid, width, height)
    snap_dead_pct = _dead_zone_pct(snap_valid, width, height)
    if cur_dead_pct is not None and snap_dead_pct is not None:
        delta_pct = round(cur_dead_pct - snap_dead_pct, 1)
        if abs(delta_pct) >= 0.5:
            if delta_pct < 0:
                findings.append({
                    "severity": "info",
                    "category": "comparison",
                    "title": "Dead zone reduction",
                    "body": f"Dead zone area reduced from {snap_dead_pct:.1f}% to {cur_dead_pct:.1f}% of floor plan.",
                    "metric": delta_pct,
                })
            else:
                findings.append({
                    "severity": "warn",
                    "category": "comparison",
                    "title": "Dead zone increase",
                    "body": f"Dead zone area increased from {snap_dead_pct:.1f}% to {cur_dead_pct:.1f}% of floor plan.",
                    "metric": delta_pct,
                })

    return findings


# ── Analyzers ────────────────────────────────────────────────────

def _analyze_extremes(points: list, rooms: list, access_points: list) -> List[dict]:
    findings = []
    rssis = [(p["sample"]["rssi"], p) for p in points]
    rssis.sort(key=lambda x: x[0])

    worst_rssi, worst_p = rssis[0]
    best_rssi, best_p = rssis[-1]

    worst_bssid = worst_p["sample"].get("bssid") or "unknown"
    best_bssid = best_p["sample"].get("bssid") or "unknown"
    worst_ap = _ap_name_for_bssid(worst_bssid, access_points)
    best_ap = _ap_name_for_bssid(best_bssid, access_points)
    worst_loc = _location_label(worst_p, rooms)
    best_loc = _location_label(best_p, rooms)
    worst_via = worst_ap or f"BSSID {worst_bssid[-8:]}"
    best_via = best_ap or f"BSSID {best_bssid[-8:]}"

    sev = "bad" if worst_rssi < POOR else ("warn" if worst_rssi < USABLE else "info")
    findings.append({
        "severity": sev,
        "category": "extremes",
        "title": "Weakest measurement",
        "body": f"Weakest reading is {worst_rssi} dBm ({rssi_label(worst_rssi)}) {worst_loc}, connected to {worst_via}.",
        "metric": worst_rssi,
    })

    findings.append({
        "severity": "info",
        "category": "extremes",
        "title": "Strongest measurement",
        "body": f"Strongest reading is {best_rssi} dBm ({rssi_label(best_rssi)}) {best_loc}, connected to {best_via}.",
        "metric": best_rssi,
    })

    return findings


def _analyze_weak_points(points: list, rooms: list) -> List[dict]:
    """Flag individual measurements below -70 dBm."""
    findings = []
    weak = [p for p in points if p["sample"]["rssi"] < USABLE]
    if not weak:
        return findings

    weak.sort(key=lambda p: p["sample"]["rssi"])
    # Report up to 5 weakest
    for p in weak[:5]:
        rssi = p["sample"]["rssi"]
        loc = _location_label(p, rooms)
        sev = "bad" if rssi < POOR else "warn"
        findings.append({
            "severity": sev,
            "category": "weak-point",
            "title": f"Weak signal {loc}",
            "body": f"{rssi} dBm ({rssi_label(rssi)}). Tx rate: {p['sample'].get('tx_rate', '?')} Mbps.",
            "metric": rssi,
        })

    return findings


def _analyze_low_tx_rate(points: list, rooms: list) -> List[dict]:
    """Flag points with Tx rate < 100 Mbps regardless of RSSI."""
    findings = []
    low = [p for p in points
           if p["sample"].get("tx_rate") is not None
           and p["sample"]["tx_rate"] < TX_RATE_LOW
           and p["sample"]["rssi"] >= USABLE]  # only flag if RSSI looks OK

    if not low:
        return findings

    low.sort(key=lambda p: p["sample"]["tx_rate"])
    for p in low[:3]:
        tx = p["sample"]["tx_rate"]
        rssi = p["sample"]["rssi"]
        loc = _location_label(p, rooms)
        findings.append({
            "severity": "warn",
            "category": "throughput",
            "title": f"Low throughput {loc}",
            "body": f"Tx rate is {tx} Mbps despite RSSI of {rssi} dBm ({rssi_label(rssi)}). Possible interference or rate-limiting.",
            "metric": tx,
        })

    return findings


def _analyze_sticky_client(points: list, access_points: list) -> List[dict]:
    """Warn when placed APs > unique BSSIDs seen in measurements."""
    findings = []
    if len(access_points) < 2:
        return findings

    unique_bssids = set()
    for p in points:
        b = p["sample"].get("bssid")
        if b:
            unique_bssids.add(b.lower())

    # Count how many APs were actually seen (fuzzy match)
    matched_aps = set()
    for b in unique_bssids:
        for i, ap in enumerate(access_points):
            if _bssid_matches(b, ap.get("bssid", "")):
                matched_aps.add(i)

    n_aps = len(access_points)
    n_bssids = len(unique_bssids)

    if n_bssids < n_aps:
        findings.append({
            "severity": "warn",
            "category": "roaming",
            "title": "Possible sticky-client behavior",
            "body": f"You have {n_aps} access points marked but only {n_bssids} unique BSSID{'s' if n_bssids != 1 else ''} appeared in {len(points)} measurements. The laptop may not be roaming between nodes, or the mesh uses a shared BSSID.",
            "metric": n_bssids,
        })

    return findings


def _analyze_ap_coverage(points: list) -> List[dict]:
    findings = []
    bssid_counts: dict = {}
    for p in points:
        b = p["sample"].get("bssid")
        if not b:
            continue
        if b not in bssid_counts:
            bssid_counts[b] = {"bssid": b, "ssid": p["sample"].get("ssid", "?"), "count": 0}
        bssid_counts[b]["count"] += 1

    total = len(points)
    if total == 0 or not bssid_counts:
        return findings

    for info in sorted(bssid_counts.values(), key=lambda x: -x["count"]):
        pct = round(info["count"] / total * 100, 1)
        label = f'{info["ssid"]} ({info["bssid"][-8:]})'
        if pct < 10:
            findings.append({
                "severity": "warn",
                "category": "coverage",
                "title": f"Underused AP: {label}",
                "body": f"Only {pct}% of measurements ({info['count']}/{total}) connected to this access point.",
                "metric": pct,
            })
        else:
            findings.append({
                "severity": "info",
                "category": "coverage",
                "title": f"AP coverage: {label}",
                "body": f"{pct}% of measurements ({info['count']}/{total}) connected to this access point.",
                "metric": pct,
            })

    return findings


def _analyze_survey_quality(points: list, width: int, height: int) -> List[dict]:
    findings = []
    n = len(points)

    cell_w = width / QUALITY_GRID
    cell_h = height / QUALITY_GRID
    occupied = set()
    for p in points:
        col = min(int(p["x"] / cell_w), QUALITY_GRID - 1)
        row = min(int(p["y"] / cell_h), QUALITY_GRID - 1)
        occupied.add((row, col))

    total_cells = QUALITY_GRID * QUALITY_GRID
    coverage_pct = round(len(occupied) / total_cells * 100, 1)

    if coverage_pct < 60:
        empty_names = []
        for row in range(QUALITY_GRID):
            for col in range(QUALITY_GRID):
                if (row, col) not in occupied:
                    empty_names.append(QUADRANT_NAMES[row][col])

        recommend = ", ".join(empty_names[:4])
        if len(empty_names) > 4:
            recommend += f" and {len(empty_names) - 4} more"

        findings.append({
            "severity": "warn",
            "category": "quality",
            "title": "Uneven point distribution",
            "body": f"Only {coverage_pct}% of floor plan cells have measurements ({len(occupied)}/{total_cells}). Add measurements in the {recommend} areas.",
            "metric": coverage_pct,
        })
    else:
        findings.append({
            "severity": "info",
            "category": "quality",
            "title": "Survey coverage",
            "body": f"{coverage_pct}% of floor plan cells have at least one measurement ({len(occupied)}/{total_cells}). {n} total measurements.",
            "metric": coverage_pct,
        })

    return findings


def _analyze_dead_zones(points: list, width: int, height: int) -> List[dict]:
    findings = []

    if len(points) < MIN_POINTS_AREA:
        findings.append({
            "severity": "info",
            "category": "dead-zone",
            "title": "Dead zone analysis",
            "body": f"Need at least {MIN_POINTS_AREA} measurements for area-based insights ({len(points)} taken so far).",
            "metric": None,
            "locked": True,
        })
        return findings

    # Two-tier: warn at -70, bad at -75
    warn_pct = _zone_pct(points, width, height, DEAD_ZONE_WARN)
    bad_pct = _zone_pct(points, width, height, DEAD_ZONE_BAD)

    if warn_pct is None:
        return findings

    if warn_pct < 1.0:
        findings.append({
            "severity": "info",
            "category": "dead-zone",
            "title": "No significant dead zones",
            "body": f"Less than 1% of the interpolated area falls below {DEAD_ZONE_WARN} dBm.",
            "metric": round(warn_pct, 1),
        })
    else:
        if bad_pct and bad_pct >= 5:
            findings.append({
                "severity": "bad",
                "category": "dead-zone",
                "title": "Critical dead zones",
                "body": f"{bad_pct:.1f}% of the floor plan falls below {DEAD_ZONE_BAD} dBm (critical). An additional {round(warn_pct - bad_pct, 1)}% is between {DEAD_ZONE_BAD} and {DEAD_ZONE_WARN} dBm (weak).",
                "metric": round(bad_pct, 1),
            })
        else:
            findings.append({
                "severity": "warn",
                "category": "dead-zone",
                "title": "Weak coverage areas",
                "body": f"{warn_pct:.1f}% of the interpolated floor plan falls below {DEAD_ZONE_WARN} dBm.",
                "metric": round(warn_pct, 1),
            })

    return findings


def _analyze_rooms(points: list, rooms: list) -> List[dict]:
    """Per-room average RSSI summary."""
    if not rooms:
        return []

    findings = []
    for room in rooms:
        in_room = [p for p in points if _room_for_point(p, [room])]
        if not in_room:
            continue
        rssis = [p["sample"]["rssi"] for p in in_room]
        avg = round(sum(rssis) / len(rssis))
        worst = min(rssis)
        sev = "bad" if avg < POOR else ("warn" if avg < USABLE else "info")
        findings.append({
            "severity": sev,
            "category": "room",
            "title": room["name"],
            "body": f"Average {avg} dBm ({rssi_label(avg)}) from {len(in_room)} measurements. Weakest: {worst} dBm.",
            "metric": avg,
        })

    return findings


# ── Helpers ──────────────────────────────────────────────────────

def _zone_pct(points, width, height, threshold):
    """Percentage of interpolated grid below a threshold."""
    triples = [(p["x"], p["y"], p["sample"]["rssi"]) for p in points
               if p.get("sample", {}).get("rssi") is not None]
    if len(triples) < MIN_POINTS_BASIC:
        return None
    try:
        grid = compute_rssi_grid(triples, width, height, resolution=100)
    except HeatmapError:
        return None
    return round(float(np.sum(grid < threshold)) / grid.size * 100, 1)


def _dead_zone_pct(points: list, width: int, height: int) -> Optional[float]:
    """Backward-compat wrapper using the warn threshold."""
    return _zone_pct(points, width, height, DEAD_ZONE_WARN)
