"""Tests for survey data operations and persistence."""
import json
import os
import tempfile
import unittest

from scanner import WifiSample
from storage import Survey, SurveyStorage, SurveyPoint, AccessPoint


def _sample(rssi=-50, bssid="aa:bb:cc:dd:ee:ff", ssid="TestNet"):
    return WifiSample(ssid=ssid, bssid=bssid, rssi=rssi, noise=-90)


class TestSurveyPoints(unittest.TestCase):
    def test_add_and_remove_point(self):
        s = Survey()
        p = s.add_point(100, 200, _sample())
        self.assertEqual(len(s.points), 1)
        self.assertEqual(p.x, 100)
        self.assertEqual(p.y, 200)

        self.assertTrue(s.remove_point(p.id))
        self.assertEqual(len(s.points), 0)

    def test_remove_nonexistent_returns_false(self):
        s = Survey()
        self.assertFalse(s.remove_point("nope"))

    def test_clear_points(self):
        s = Survey()
        s.add_point(0, 0, _sample())
        s.add_point(1, 1, _sample())
        s.clear_points()
        self.assertEqual(len(s.points), 0)

    def test_add_point_with_multi_scan(self):
        s = Survey()
        samples = [{"rssi": -40, "bssid": "aa"}, {"rssi": -42, "bssid": "bb"}]
        p = s.add_point(10, 20, _sample(), all_samples=samples, bssid_changed=True)
        self.assertEqual(p.all_samples, samples)
        self.assertTrue(p.bssid_changed)

    def test_add_point_defaults_multi_scan(self):
        s = Survey()
        p = s.add_point(10, 20, _sample())
        self.assertEqual(p.all_samples, [])
        self.assertFalse(p.bssid_changed)


class TestSurveyAccessPoints(unittest.TestCase):
    def test_add_and_remove(self):
        s = Survey()
        ap = s.add_access_point(300, 400, "Living Room", bssid="11:22:33")
        self.assertEqual(len(s.access_points), 1)
        self.assertEqual(ap.name, "Living Room")

        self.assertTrue(s.remove_access_point(ap.id))
        self.assertEqual(len(s.access_points), 0)

    def test_remove_nonexistent_returns_false(self):
        s = Survey()
        self.assertFalse(s.remove_access_point("nope"))


class TestUniqueBssids(unittest.TestCase):
    def test_counts_and_sorts_by_frequency(self):
        s = Survey()
        s.add_point(0, 0, _sample(bssid="aa:11", ssid="Net"))
        s.add_point(1, 1, _sample(bssid="aa:11", ssid="Net"))
        s.add_point(2, 2, _sample(bssid="bb:22", ssid="Net"))

        bssids = s.get_unique_bssids()
        self.assertEqual(len(bssids), 2)
        self.assertEqual(bssids[0]["bssid"], "aa:11")
        self.assertEqual(bssids[0]["count"], 2)
        self.assertEqual(bssids[1]["bssid"], "bb:22")
        self.assertEqual(bssids[1]["count"], 1)

    def test_skips_points_without_bssid(self):
        s = Survey()
        s.add_point(0, 0, _sample(bssid=None))
        self.assertEqual(s.get_unique_bssids(), [])


class TestSurveyStorage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "survey.json")
        self.storage = SurveyStorage(self.path)

    def test_load_missing_file_returns_empty(self):
        s = self.storage.load()
        self.assertIsNone(s.floorplan)
        self.assertEqual(len(s.points), 0)

    def test_roundtrip_preserves_all_fields(self):
        survey = Survey(floorplan="floor.png")
        survey.add_point(10, 20, _sample(-45, "aa:bb"),
                         all_samples=[{"rssi": -45}, {"rssi": -47}],
                         bssid_changed=True)
        survey.add_access_point(50, 60, "Router", bssid="cc:dd")

        self.storage.save(survey)
        loaded = self.storage.load()

        self.assertEqual(loaded.floorplan, "floor.png")
        self.assertEqual(len(loaded.points), 1)
        self.assertEqual(len(loaded.access_points), 1)

        p = loaded.points[0]
        self.assertEqual(p.x, 10)
        self.assertEqual(p.sample.rssi, -45)
        self.assertEqual(p.sample.bssid, "aa:bb")
        self.assertEqual(len(p.all_samples), 2)
        self.assertTrue(p.bssid_changed)

        ap = loaded.access_points[0]
        self.assertEqual(ap.name, "Router")
        self.assertEqual(ap.bssid, "cc:dd")

    def test_corrupt_file_backs_up_and_returns_empty(self):
        with open(self.path, "w") as f:
            f.write("{{{not json!!!")

        s = self.storage.load()
        self.assertEqual(len(s.points), 0)
        self.assertTrue(os.path.exists(self.path + ".corrupt"))

    def test_backward_compat_missing_new_fields(self):
        """Old survey.json without all_samples/bssid_changed/access_points."""
        old_data = {
            "floorplan": "fp.png",
            "points": [{
                "id": "abc",
                "x": 1, "y": 2,
                "timestamp": 1000.0,
                "sample": {"ssid": "X", "bssid": "Y", "rssi": -50,
                           "noise": None, "channel": None, "phy_mode": None,
                           "tx_rate": None, "security": None, "mcs_index": None},
            }],
        }
        with open(self.path, "w") as f:
            json.dump(old_data, f)

        loaded = self.storage.load()
        self.assertEqual(len(loaded.points), 1)
        self.assertEqual(loaded.points[0].all_samples, [])
        self.assertFalse(loaded.points[0].bssid_changed)
        self.assertEqual(loaded.access_points, [])

    def test_atomic_write(self):
        """After save, no .tmp file should remain."""
        survey = Survey()
        survey.add_point(0, 0, _sample())
        self.storage.save(survey)
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertTrue(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
