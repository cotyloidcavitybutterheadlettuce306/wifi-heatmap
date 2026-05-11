"""Integration tests for Flask API routes.

Uses Flask test client with mocked scanner to avoid needing sudo/wdutil.
"""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from PIL import Image

from scanner import WifiSample, ScanError
from storage import Survey, SurveyStorage

# Must import app module (not just the Flask app) so we can swap globals
import app as app_module


def _make_floorplan(path, w=200, h=150):
    """Create a minimal PNG floorplan for testing."""
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    img.save(path, format="PNG")


def _sample(rssi=-50, bssid="aa:bb:cc:dd:ee:ff"):
    return WifiSample(ssid="TestNet", bssid=bssid, rssi=rssi, noise=-90,
                      channel="5g40", phy_mode="11ac", tx_rate=300.0)


class APITestBase(unittest.TestCase):
    """Base class that resets app state for each test."""

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

    def _setup_floorplan(self, w=200, h=150):
        st = app_module.app.config["APP_STATE"]
        _make_floorplan(st.floorplan_path, w, h)
        st.survey.floorplan = "floorplan.png"

    def _auth(self):
        """Set a mock scanner so auth-gated routes work."""
        st = app_module.app.config["APP_STATE"]
        mock = MagicMock()
        mock.scan.return_value = _sample()
        st.scanner = mock
        return mock


class TestAccessPointsCRUD(APITestBase):
    def test_create_list_delete(self):
        self._setup_floorplan()

        # Create
        r = self.client.post("/api/access_points",
                             json={"x": 50, "y": 60, "name": "Router A", "bssid": "11:22"})
        self.assertEqual(r.status_code, 200)
        ap_id = r.get_json()["access_point"]["id"]

        # List
        r = self.client.get("/api/access_points")
        aps = r.get_json()["access_points"]
        self.assertEqual(len(aps), 1)
        self.assertEqual(aps[0]["name"], "Router A")

        # Delete
        r = self.client.delete(f"/api/access_points/{ap_id}")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/access_points")
        self.assertEqual(len(r.get_json()["access_points"]), 0)

    def test_create_requires_name(self):
        self._setup_floorplan()
        r = self.client.post("/api/access_points", json={"x": 1, "y": 1, "name": ""})
        self.assertEqual(r.status_code, 400)

    def test_create_rejects_out_of_bounds(self):
        self._setup_floorplan(w=100, h=100)
        r = self.client.post("/api/access_points",
                             json={"x": 999, "y": 50, "name": "Bad"})
        self.assertEqual(r.status_code, 400)

    def test_delete_nonexistent_returns_404(self):
        r = self.client.delete("/api/access_points/nope")
        self.assertEqual(r.status_code, 404)


class TestMultiScanSelection(APITestBase):
    """Test the 3-scan measurement logic: strongest RSSI vs last-on-roam."""

    def test_selects_strongest_rssi_same_bssid(self):
        self._setup_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            _sample(rssi=-50, bssid="aa:aa"),
            _sample(rssi=-42, bssid="aa:aa"),
            _sample(rssi=-48, bssid="aa:aa"),
        ]

        r = self.client.post("/api/measure", json={"x": 10, "y": 10})
        point = r.get_json()["point"]
        self.assertEqual(point["sample"]["rssi"], -42)  # strongest
        self.assertFalse(point["bssid_changed"])
        self.assertEqual(len(point["all_samples"]), 3)

    def test_selects_last_reading_when_bssid_changes(self):
        self._setup_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            _sample(rssi=-40, bssid="aa:aa"),  # strongest but old AP
            _sample(rssi=-45, bssid="aa:aa"),
            _sample(rssi=-50, bssid="bb:bb"),  # weaker but roamed
        ]

        r = self.client.post("/api/measure", json={"x": 10, "y": 10})
        point = r.get_json()["point"]
        self.assertEqual(point["sample"]["rssi"], -50)  # last reading wins
        self.assertEqual(point["sample"]["bssid"], "bb:bb")
        self.assertTrue(point["bssid_changed"])

    def test_survives_partial_scan_failures(self):
        self._setup_floorplan()
        mock = self._auth()
        mock.scan.side_effect = [
            ScanError("timeout"),
            _sample(rssi=-55),
            ScanError("timeout"),
        ]

        r = self.client.post("/api/measure", json={"x": 10, "y": 10})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["point"]["sample"]["rssi"], -55)

    def test_all_scans_fail_returns_500(self):
        self._setup_floorplan()
        mock = self._auth()
        mock.scan.side_effect = ScanError("wifi off")

        r = self.client.post("/api/measure", json={"x": 10, "y": 10})
        self.assertEqual(r.status_code, 500)


class TestAuthGuard(APITestBase):
    def test_measure_requires_auth(self):
        r = self.client.post("/api/measure", json={"x": 0, "y": 0})
        self.assertEqual(r.status_code, 401)

    def test_scan_requires_auth(self):
        r = self.client.get("/api/scan")
        self.assertEqual(r.status_code, 401)


class TestExport(APITestBase):
    def test_export_returns_valid_png(self):
        self._setup_floorplan(200, 150)

        r = self.client.get("/api/export")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "image/png")

        img = Image.open(io.BytesIO(r.data))
        self.assertEqual(img.size, (200, 150))

    def test_export_includes_markers_in_image(self):
        self._setup_floorplan(200, 150)
        st = app_module.app.config["APP_STATE"]
        st.survey.add_point(100, 75, _sample(-45))
        st.storage.save(st.survey)

        r = self.client.get("/api/export")
        self.assertEqual(r.status_code, 200)
        img = Image.open(io.BytesIO(r.data))
        # The marker at (100, 75) should have drawn colored pixels there.
        # Check the pixel isn't pure white anymore.
        pixel = img.getpixel((100, 75))
        self.assertNotEqual(pixel, (255, 255, 255, 255))

    def test_export_no_floorplan_returns_400(self):
        r = self.client.get("/api/export")
        self.assertEqual(r.status_code, 400)

    def test_export_has_download_filename(self):
        self._setup_floorplan()
        r = self.client.get("/api/export")
        cd = r.headers.get("Content-Disposition", "")
        self.assertIn("wifi-survey-", cd)
        self.assertIn(".png", cd)


class TestStatus(APITestBase):
    def test_status_no_floorplan(self):
        r = self.client.get("/api/status")
        data = r.get_json()
        self.assertFalse(data["authenticated"])
        self.assertFalse(data["has_floorplan"])

    def test_status_with_floorplan(self):
        self._setup_floorplan(300, 200)
        r = self.client.get("/api/status")
        data = r.get_json()
        self.assertTrue(data["has_floorplan"])
        self.assertEqual(data["floorplan_width"], 300)
        self.assertEqual(data["floorplan_height"], 200)


class TestLiveScan(APITestBase):
    def test_scan_returns_sample_with_color(self):
        self._auth()
        r = self.client.get("/api/scan")
        data = r.get_json()
        self.assertEqual(data["rssi"], -50)
        self.assertIn("color", data)
        self.assertTrue(data["color"].startswith("#"))


class TestHelpers(unittest.TestCase):
    """Test the export helper functions."""

    def test_hex_to_rgb(self):
        from export import hex_to_rgb
        self.assertEqual(hex_to_rgb("#ff0000"), (255, 0, 0))
        self.assertEqual(hex_to_rgb("#00ff00"), (0, 255, 0))
        self.assertEqual(hex_to_rgb("1a2b3c"), (26, 43, 60))

    def test_bssid_color_rgb_stable(self):
        """Same BSSID always produces the same color."""
        from export import bssid_color_rgb
        c1 = bssid_color_rgb("aa:bb:cc:dd:ee:ff")
        c2 = bssid_color_rgb("aa:bb:cc:dd:ee:ff")
        self.assertEqual(c1, c2)

    def test_bssid_color_rgb_different_bssids(self):
        from export import bssid_color_rgb
        c1 = bssid_color_rgb("aa:bb:cc:dd:ee:ff")
        c2 = bssid_color_rgb("11:22:33:44:55:66")
        self.assertNotEqual(c1, c2)

    def test_bssid_color_rgb_none(self):
        from export import bssid_color_rgb
        self.assertEqual(bssid_color_rgb(None), (100, 100, 100))


if __name__ == "__main__":
    unittest.main()
