"""
Integration tests — multi-step workflows through the Flask API.

Tests that modules wire together correctly: routes → storage → insights → heatmap.
Scanner is the only mock (needs sudo + real WiFi).

Sections:
  A. Survey lifecycle
  B. Multi-scan measurement
  C. Access points + BSSID linking
  D. Insights auto-recompute
  E. Snapshots + comparison
  F. Heatmap + export
  G. Auth gating
  H. Clean state on load
  I. Save and restore
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from PIL import Image

from scanner import WifiSample, ScanError
from storage import Survey, SurveyStorage

import app as app_module


# ── Helpers ──────────────────────────────────────────────────────

def _sample(rssi=-50, bssid="aa:bb:cc:dd:ee:ff", ssid="TestNet"):
    return WifiSample(ssid=ssid, bssid=bssid, rssi=rssi, noise=-90,
                      channel="5g40", phy_mode="11ac", tx_rate=300.0,
                      security="WPA2", mcs_index=7)


class IntegrationBase(unittest.TestCase):
    """Shared setup: temp dir, fresh app state, mock scanner, helper methods."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_state = app_module.app.config["APP_STATE"]

        snapshots_dir = os.path.join(self.tmpdir, "snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)

        app_module.app.config["APP_STATE"] = app_module.AppState(
            survey_path=os.path.join(self.tmpdir, "survey.json"),
            floorplan_path=os.path.join(self.tmpdir, "floorplan.png"),
            snapshots_dir=snapshots_dir,
            storage=SurveyStorage(os.path.join(self.tmpdir, "survey.json")),
            survey=Survey(),
        )
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.app.config["APP_STATE"] = self._orig_state

    # ── Helper methods ───────────────────────────────────────────

    def _upload_floorplan(self, w=400, h=300):
        """Create and upload a minimal PNG floorplan."""
        img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        r = self.client.post("/api/floorplan", data={
            "file": (buf, "floor.png", "image/png"),
        }, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _auth(self, bssid="aa:bb:cc:dd:ee:ff", rssi=-50):
        """Install a mock scanner so auth-gated routes work."""
        st = app_module.app.config["APP_STATE"]
        mock = MagicMock()
        mock.scan.return_value = _sample(rssi=rssi, bssid=bssid)
        st.scanner = mock
        return mock

    def _measure_at(self, x, y, rssi=-50, bssid="aa:bb:cc:dd:ee:ff"):
        """Take a measurement at (x, y) with controlled scan results."""
        mock = self._auth(bssid=bssid, rssi=rssi)
        # All 3 multi-scan readings return the same value
        mock.scan.side_effect = [
            _sample(rssi=rssi, bssid=bssid),
            _sample(rssi=rssi, bssid=bssid),
            _sample(rssi=rssi, bssid=bssid),
        ]
        r = self.client.post("/api/measure", json={"x": x, "y": y})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["point"]

    def _add_ap(self, x, y, name, bssid=""):
        r = self.client.post("/api/access_points",
                             json={"x": x, "y": y, "name": name, "bssid": bssid})
        self.assertEqual(r.status_code, 200)
        return r.get_json()["access_point"]

    def _get_insights(self, bssid=None):
        params = f"?bssid={bssid}" if bssid else ""
        r = self.client.get(f"/api/insights{params}")
        self.assertEqual(r.status_code, 200)
        return r.get_json()["findings"]

    def _save_snapshot(self, name="Test Snapshot"):
        r = self.client.post("/api/snapshots", json={"name": name})
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _get_status(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def _get_points(self):
        r = self.client.get("/api/points")
        self.assertEqual(r.status_code, 200)
        return r.get_json()["points"]

    def _get_aps(self):
        r = self.client.get("/api/access_points")
        self.assertEqual(r.status_code, 200)
        return r.get_json()["access_points"]


# ── A. Survey lifecycle ──────────────────────────────────────────

class TestSurveyLifecycle(IntegrationBase):

    def test_01_upload_measure_verify(self):
        """Upload floorplan → measure → points and status update."""
        self._upload_floorplan()
        self._auth()

        pt = self._measure_at(100, 100, rssi=-45)
        self.assertEqual(pt["sample"]["rssi"], -45)

        points = self._get_points()
        self.assertEqual(len(points), 1)

        status = self._get_status()
        self.assertEqual(status["point_count"], 1)
        self.assertTrue(status["has_floorplan"])

    def test_02_new_floorplan_clears_points(self):
        """Uploading a new floor plan wipes all existing points."""
        self._upload_floorplan()
        self._auth()
        self._measure_at(50, 50)
        self.assertEqual(len(self._get_points()), 1)

        # Upload new floorplan
        self._upload_floorplan(w=800, h=600)
        self.assertEqual(len(self._get_points()), 0)
        status = self._get_status()
        self.assertEqual(status["floorplan_width"], 800)

    def test_03_delete_single_point(self):
        """Measure → delete → gone from points and bssids."""
        self._upload_floorplan()
        pt = self._measure_at(100, 100, bssid="aa:aa:aa:aa:aa:aa")

        r = self.client.delete(f"/api/points/{pt['id']}")
        self.assertEqual(r.status_code, 200)

        self.assertEqual(len(self._get_points()), 0)
        bssids = self.client.get("/api/bssids").get_json()["bssids"]
        self.assertEqual(len(bssids), 0)

    def test_04_clear_all_points(self):
        """Measure several → clear all → clean state."""
        self._upload_floorplan()
        for i in range(4):
            self._measure_at(50 + i * 80, 50, rssi=-40 - i * 5)

        self.assertEqual(len(self._get_points()), 4)

        r = self.client.post("/api/clear")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self._get_points()), 0)
        self.assertEqual(self._get_status()["point_count"], 0)


# ── B. Multi-scan measurement ────────────────────────────────────

class TestMultiScan(IntegrationBase):

    def test_05_strongest_rssi_same_bssid(self):
        """3 scans same BSSID → strongest selected."""
        self._upload_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            _sample(rssi=-55, bssid="aa:aa"),
            _sample(rssi=-42, bssid="aa:aa"),
            _sample(rssi=-50, bssid="aa:aa"),
        ]
        r = self.client.post("/api/measure", json={"x": 100, "y": 100})
        pt = r.get_json()["point"]
        self.assertEqual(pt["sample"]["rssi"], -42)
        self.assertFalse(pt["bssid_changed"])
        self.assertEqual(len(pt["all_samples"]), 3)

    def test_06_bssid_roam_uses_last(self):
        """BSSID changes → last reading used, bssid_changed=true."""
        self._upload_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            _sample(rssi=-40, bssid="aa:aa"),
            _sample(rssi=-42, bssid="aa:aa"),
            _sample(rssi=-55, bssid="bb:bb"),
        ]
        r = self.client.post("/api/measure", json={"x": 100, "y": 100})
        pt = r.get_json()["point"]
        self.assertEqual(pt["sample"]["bssid"], "bb:bb")
        self.assertTrue(pt["bssid_changed"])

    def test_07_partial_failures(self):
        """Some scans fail → works with survivors."""
        self._upload_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            ScanError("timeout"),
            _sample(rssi=-60),
            ScanError("timeout"),
        ]
        r = self.client.post("/api/measure", json={"x": 100, "y": 100})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["point"]["sample"]["rssi"], -60)


# ── C. Access points + BSSID linking ─────────────────────────────

class TestAccessPoints(IntegrationBase):

    def test_08_ap_bssid_linking(self):
        """Create AP with BSSID → measure on same BSSID → bssids endpoint reflects both."""
        self._upload_floorplan()
        bssid = "11:22:33:44:55:66"
        self._add_ap(200, 150, "Kitchen Deco", bssid=bssid)
        self._measure_at(100, 100, bssid=bssid)

        bssids = self.client.get("/api/bssids").get_json()["bssids"]
        self.assertEqual(len(bssids), 1)
        self.assertEqual(bssids[0]["bssid"], bssid)

        aps = self._get_aps()
        self.assertEqual(aps[0]["bssid"], bssid)

    def test_09_create_delete_ap(self):
        """Create AP → delete → clean."""
        self._upload_floorplan()
        ap = self._add_ap(100, 100, "Test AP")

        r = self.client.delete(f"/api/access_points/{ap['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self._get_aps()), 0)

    def test_10_ap_out_of_bounds(self):
        """AP with coords outside floorplan → rejected."""
        self._upload_floorplan(w=200, h=200)
        r = self.client.post("/api/access_points",
                             json={"x": 999, "y": 100, "name": "Bad"})
        self.assertEqual(r.status_code, 400)

    def test_11_clear_all_aps(self):
        """Create multiple APs → delete all → empty list."""
        self._upload_floorplan()
        ap1 = self._add_ap(50, 50, "AP1")
        ap2 = self._add_ap(100, 100, "AP2")
        self.assertEqual(len(self._get_aps()), 2)

        self.client.delete(f"/api/access_points/{ap1['id']}")
        self.client.delete(f"/api/access_points/{ap2['id']}")
        self.assertEqual(len(self._get_aps()), 0)


# ── D. Insights auto-recompute ───────────────────────────────────

class TestInsights(IntegrationBase):

    def test_12_too_few_points_empty(self):
        """<3 points → insights returns empty."""
        self._upload_floorplan()
        self._measure_at(100, 100)
        self._measure_at(200, 100)
        findings = self._get_insights()
        self.assertEqual(findings, [])

    def test_13_findings_appear_with_enough_points(self):
        """≥3 points → extremes, quality, coverage findings present."""
        self._upload_floorplan()
        self._measure_at(50, 50, rssi=-40, bssid="aa:aa")
        self._measure_at(200, 50, rssi=-60, bssid="aa:aa")
        self._measure_at(350, 250, rssi=-72, bssid="bb:bb")

        findings = self._get_insights()
        categories = {f["category"] for f in findings}
        self.assertIn("extremes", categories)
        self.assertIn("quality", categories)
        self.assertIn("coverage", categories)
        self.assertIn("dead-zone", categories)

        # Extremes should have correct values
        weakest = next(f for f in findings if f["title"] == "Weakest measurement")
        self.assertEqual(weakest["metric"], -72)
        strongest = next(f for f in findings if f["title"] == "Strongest measurement")
        self.assertEqual(strongest["metric"], -40)

    def test_14_dead_zone_unlocks_at_15(self):
        """<15 points → dead zone locked. ≥15 → unlocked with real analysis."""
        self._upload_floorplan()
        # Add 5 points — should be locked
        for i in range(5):
            self._measure_at(50 + i * 60, 50 + i * 40, rssi=-50)
        findings = self._get_insights()
        dz = [f for f in findings if f["category"] == "dead-zone"]
        self.assertTrue(dz[0].get("locked"))

        # Add 10 more (total 15) — should unlock
        for i in range(10):
            self._measure_at(50 + i * 30, 200, rssi=-50)
        findings = self._get_insights()
        dz = [f for f in findings if f["category"] == "dead-zone"]
        self.assertFalse(dz[0].get("locked", False))

    def test_15_bssid_filter_on_insights(self):
        """Filter insights by BSSID → only that BSSID's points analyzed."""
        self._upload_floorplan()
        self._measure_at(50, 50, rssi=-40, bssid="aa:aa")
        self._measure_at(200, 50, rssi=-40, bssid="aa:aa")
        self._measure_at(350, 250, rssi=-75, bssid="bb:bb")

        # Filter to bb:bb only — not enough points for findings
        findings = self._get_insights(bssid="bb:bb")
        self.assertEqual(findings, [])

        # Filter to aa:aa — 2 points, also not enough
        findings = self._get_insights(bssid="aa:aa")
        self.assertEqual(findings, [])

        # Add one more aa:aa — now 3 points for that BSSID
        self._measure_at(150, 150, rssi=-55, bssid="aa:aa")
        findings = self._get_insights(bssid="aa:aa")
        self.assertTrue(len(findings) > 0)
        # All coverage findings should only reference aa:aa
        cov = [f for f in findings if f["category"] == "coverage"]
        for f in cov:
            self.assertIn("aa:aa", f["title"])

    def test_16_insights_update_after_delete(self):
        """Delete points → insights change."""
        self._upload_floorplan()
        pts = []
        for i in range(3):
            pts.append(self._measure_at(50 + i * 100, 50, rssi=-40 - i * 15))

        findings_before = self._get_insights()
        weakest_before = next(f for f in findings_before if f["title"] == "Weakest measurement")

        # Delete the weakest point
        self.client.delete(f"/api/points/{pts[2]['id']}")

        findings_after = self._get_insights()
        # With only 2 points, should be empty now
        self.assertEqual(findings_after, [])


# ── E. Snapshots + comparison ────────────────────────────────────

class TestSnapshots(IntegrationBase):

    def test_17_save_list_get(self):
        """Save snapshot → list → get with findings."""
        self._upload_floorplan()
        for i in range(3):
            self._measure_at(50 + i * 100, 50, rssi=-50)

        snap = self._save_snapshot("My Baseline")
        self.assertEqual(snap["name"], "My Baseline")

        # List
        r = self.client.get("/api/snapshots")
        snaps = r.get_json()["snapshots"]
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["point_count"], 3)

        # Get with findings
        r = self.client.get(f"/api/snapshots/{snap['slug']}")
        data = r.get_json()
        self.assertEqual(data["name"], "My Baseline")
        self.assertEqual(len(data["survey"]["points"]), 3)
        self.assertTrue(len(data["findings"]) > 0)

    def test_18_compare_shows_diff(self):
        """Save snapshot → add points → compare shows diff."""
        self._upload_floorplan()
        for i in range(3):
            self._measure_at(50 + i * 100, 50, rssi=-60)
        snap = self._save_snapshot("Before")

        # Add more points with better signal
        for i in range(3):
            self._measure_at(50 + i * 100, 200, rssi=-40)

        # Get snapshot — should include diff
        r = self.client.get(f"/api/snapshots/{snap['slug']}")
        data = r.get_json()
        diff = data["diff"]
        self.assertTrue(len(diff) > 0)

        # Should have point count diff
        count_diff = [d for d in diff if "count" in d["title"].lower()]
        self.assertTrue(len(count_diff) > 0)

        # Should have RSSI improvement
        rssi_diff = [d for d in diff if "signal" in d["title"].lower()]
        self.assertTrue(len(rssi_diff) > 0)
        self.assertIn("improved", rssi_diff[0]["body"])

    def test_19_delete_snapshot(self):
        """Delete snapshot → gone from list."""
        self._upload_floorplan()
        snap = self._save_snapshot("To Delete")

        r = self.client.delete(f"/api/snapshots/{snap['slug']}")
        self.assertEqual(r.status_code, 200)

        snaps = self.client.get("/api/snapshots").get_json()["snapshots"]
        self.assertEqual(len(snaps), 0)

    def test_20_duplicate_name_unique_slug(self):
        """Same name twice → unique slugs."""
        self._upload_floorplan()
        s1 = self._save_snapshot("Same Name")
        s2 = self._save_snapshot("Same Name")
        self.assertNotEqual(s1["slug"], s2["slug"])

        snaps = self.client.get("/api/snapshots").get_json()["snapshots"]
        self.assertEqual(len(snaps), 2)


# ── F. Heatmap + export ──────────────────────────────────────────

class TestHeatmapExport(IntegrationBase):

    def test_21_heatmap_too_few_points(self):
        """<3 points → heatmap returns error."""
        self._upload_floorplan()
        self._measure_at(100, 100)
        r = self.client.get("/api/heatmap")
        self.assertEqual(r.status_code, 400)

    def test_22_heatmap_with_enough_points(self):
        """≥3 points → valid PNG."""
        self._upload_floorplan()
        for i in range(3):
            self._measure_at(50 + i * 100, 100, rssi=-50)
        r = self.client.get("/api/heatmap?alpha=0.6")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "image/png")
        img = Image.open(io.BytesIO(r.data))
        self.assertEqual(img.size, (400, 300))

    def test_23_export_no_floorplan(self):
        """Export without floorplan → 400."""
        r = self.client.get("/api/export")
        self.assertEqual(r.status_code, 400)

    def test_24_export_with_points(self):
        """Export with points → valid PNG with markers."""
        self._upload_floorplan(400, 300)
        self._measure_at(200, 150, rssi=-45)
        r = self.client.get("/api/export")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "image/png")
        img = Image.open(io.BytesIO(r.data))
        self.assertEqual(img.size, (400, 300))
        # Marker should have drawn non-white pixels at (200, 150)
        px = img.getpixel((200, 150))
        self.assertNotEqual(px, (255, 255, 255, 255))

    def test_25_export_with_heatmap(self):
        """Export with heatmap=true → composited PNG."""
        self._upload_floorplan(400, 300)
        for i in range(3):
            self._measure_at(50 + i * 100, 100, rssi=-50)
        r = self.client.get("/api/export?heatmap=true&alpha=0.5")
        self.assertEqual(r.status_code, 200)
        img = Image.open(io.BytesIO(r.data))
        self.assertEqual(img.size, (400, 300))


# ── G. Auth gating ───────────────────────────────────────────────

class TestAuthGating(IntegrationBase):

    def test_26_measure_requires_auth(self):
        self._upload_floorplan()
        r = self.client.post("/api/measure", json={"x": 100, "y": 100})
        self.assertEqual(r.status_code, 401)

    def test_27_scan_requires_auth(self):
        r = self.client.get("/api/scan")
        self.assertEqual(r.status_code, 401)

    def test_28_non_auth_endpoints_work(self):
        """insights, snapshots, access_points don't need auth."""
        self._upload_floorplan()

        r = self.client.get("/api/insights")
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/api/snapshots")
        self.assertEqual(r.status_code, 200)

        r = self.client.post("/api/access_points",
                             json={"x": 50, "y": 50, "name": "Test"})
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/api/access_points")
        self.assertEqual(r.status_code, 200)


# ── H. Clean state on load ───────────────────────────────────────

class TestCleanState(IntegrationBase):

    def test_29_fresh_start(self):
        """No survey.json → empty everything."""
        status = self._get_status()
        self.assertFalse(status["has_floorplan"])
        self.assertEqual(status["point_count"], 0)
        self.assertFalse(status["authenticated"])

        self.assertEqual(self._get_points(), [])
        self.assertEqual(self._get_aps(), [])
        self.assertEqual(self._get_insights(), [])
        self.assertEqual(
            self.client.get("/api/snapshots").get_json()["snapshots"], [])

    def test_30_load_existing_data(self):
        """Pre-existing survey.json → status reflects correct state."""
        self._upload_floorplan(500, 400)
        self._measure_at(100, 100)
        self._measure_at(200, 200)
        self._add_ap(250, 200, "Router")

        # Simulate server restart: new storage + load from disk
        st = app_module.app.config["APP_STATE"]
        st.storage = SurveyStorage(st.survey_path)
        st.survey = st.storage.load()

        status = self._get_status()
        self.assertTrue(status["has_floorplan"])
        self.assertEqual(status["point_count"], 2)
        self.assertEqual(len(self._get_points()), 2)
        self.assertEqual(len(self._get_aps()), 1)


# ── I. Save and restore ─────────────────────────────────────────

class TestSaveRestore(IntegrationBase):

    def test_31_persistence_survives_restart(self):
        """Points + APs persist across simulated restarts."""
        self._upload_floorplan()
        self._measure_at(100, 100, rssi=-45, bssid="aa:aa")
        self._measure_at(200, 200, rssi=-55, bssid="bb:bb")
        self._add_ap(150, 150, "Main Router", bssid="aa:aa")

        # Simulate restart
        app_module.storage = SurveyStorage(app_module.SURVEY_PATH)
        app_module.survey = app_module.storage.load()

        points = self._get_points()
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["sample"]["rssi"], -45)

        aps = self._get_aps()
        self.assertEqual(len(aps), 1)
        self.assertEqual(aps[0]["name"], "Main Router")
        self.assertEqual(aps[0]["bssid"], "aa:aa")

    def test_32_restore_from_snapshot(self):
        """Save snapshot → clear → restore → data matches original."""
        self._upload_floorplan()
        self._measure_at(100, 100, rssi=-42, bssid="aa:aa")
        self._measure_at(200, 200, rssi=-55, bssid="bb:bb")
        self._add_ap(150, 150, "Router", bssid="aa:aa")

        snap = self._save_snapshot("Before Reset")

        # Clear everything
        self.client.post("/api/clear")
        self.assertEqual(len(self._get_points()), 0)

        # Restore
        r = self.client.post(f"/api/snapshots/{snap['slug']}/restore")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["point_count"], 2)

        # Verify data matches original
        points = self._get_points()
        self.assertEqual(len(points), 2)
        rssis = sorted([p["sample"]["rssi"] for p in points])
        self.assertEqual(rssis, [-55, -42])

        # APs should also be restored
        aps = self._get_aps()
        self.assertEqual(len(aps), 1)
        self.assertEqual(aps[0]["name"], "Router")


class TestRooms(IntegrationBase):

    def test_33_room_crud(self):
        """Create room → list → delete."""
        self._upload_floorplan()
        r = self.client.post("/api/rooms", json={
            "name": "Kitchen", "x1": 10, "y1": 10, "x2": 200, "y2": 150,
        })
        self.assertEqual(r.status_code, 200)
        room_id = r.get_json()["room"]["id"]
        # Coordinates should be normalized (min/max)
        room = r.get_json()["room"]
        self.assertEqual(room["x1"], 10)
        self.assertEqual(room["x2"], 200)

        rooms = self.client.get("/api/rooms").get_json()["rooms"]
        self.assertEqual(len(rooms), 1)

        self.client.delete(f"/api/rooms/{room_id}")
        rooms = self.client.get("/api/rooms").get_json()["rooms"]
        self.assertEqual(len(rooms), 0)

    def test_34_room_insights(self):
        """Rooms provide per-room RSSI summaries in insights."""
        self._upload_floorplan()
        self.client.post("/api/rooms", json={
            "name": "Office", "x1": 0, "y1": 0, "x2": 200, "y2": 150,
        })
        self._measure_at(100, 75, rssi=-55)
        self._measure_at(150, 100, rssi=-65)
        self._measure_at(300, 200, rssi=-40)  # outside room

        findings = self._get_insights()
        room_f = [f for f in findings if f["category"] == "room"]
        self.assertEqual(len(room_f), 1)
        self.assertEqual(room_f[0]["title"], "Office")
        self.assertEqual(room_f[0]["metric"], -60)  # avg of -55, -65

    def test_35_rooms_persist(self):
        """Rooms survive server restart."""
        self._upload_floorplan()
        self.client.post("/api/rooms", json={
            "name": "Bedroom", "x1": 50, "y1": 50, "x2": 300, "y2": 200,
        })
        # Simulate restart
        app_module.storage = SurveyStorage(app_module.SURVEY_PATH)
        app_module.survey = app_module.storage.load()

        rooms = self.client.get("/api/rooms").get_json()["rooms"]
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["name"], "Bedroom")


if __name__ == "__main__":
    unittest.main()
