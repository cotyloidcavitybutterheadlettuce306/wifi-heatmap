"""Tests for the insights engine."""
import unittest

from insights import (
    compute_insights, compare_surveys, rssi_label,
    MIN_POINTS_AREA, POOR, USABLE, TX_RATE_LOW,
)


def _pt(x, y, rssi, bssid="aa:bb:cc:dd:ee:ff", ssid="TestNet", tx_rate=300.0):
    """Helper to build a point dict matching the format insights.py expects."""
    return {
        "x": x, "y": y,
        "sample": {"rssi": rssi, "bssid": bssid, "ssid": ssid, "tx_rate": tx_rate},
    }


def _room(name, x1, y1, x2, y2):
    return {"id": "r1", "name": name, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


class TestRssiLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(rssi_label(-30), "excellent")
        self.assertEqual(rssi_label(-55), "good")
        self.assertEqual(rssi_label(-65), "usable")
        self.assertEqual(rssi_label(-72), "poor")
        self.assertEqual(rssi_label(-80), "dead")


class TestExtremes(unittest.TestCase):
    def test_weakest_and_strongest(self):
        points = [
            _pt(10, 10, -40, bssid="aa:aa"),
            _pt(50, 50, -70, bssid="bb:bb"),
            _pt(90, 90, -55, bssid="aa:aa"),
        ]
        findings = compute_insights(points, 100, 100)
        titles = {f["title"]: f for f in findings}

        self.assertIn("Weakest measurement", titles)
        self.assertEqual(titles["Weakest measurement"]["metric"], -70)

        self.assertIn("Strongest measurement", titles)
        self.assertEqual(titles["Strongest measurement"]["metric"], -40)

    def test_weakest_severity_matches_threshold(self):
        """Weakest below -75 should be 'bad'."""
        points = [_pt(i * 10, 10, -80) for i in range(3)]
        findings = compute_insights(points, 100, 100)
        weakest = next(f for f in findings if f["title"] == "Weakest measurement")
        self.assertEqual(weakest["severity"], "bad")


class TestAPCoverage(unittest.TestCase):
    def test_flags_underused_ap(self):
        # 11 points on AP-A, 1 on AP-B -> AP-B is ~8.3%, under 10%
        points = [_pt(i * 10, 10, -50, bssid="ap-a") for i in range(11)]
        points.append(_pt(95, 10, -50, bssid="ap-b"))
        findings = compute_insights(points, 100, 100)

        underused = [f for f in findings if "Underused" in f["title"]]
        self.assertEqual(len(underused), 1)
        self.assertIn("ap-b", underused[0]["title"])
        self.assertEqual(underused[0]["severity"], "warn")

    def test_no_flag_when_balanced(self):
        points = [_pt(i * 10, 10, -50, bssid="ap-a") for i in range(5)]
        points += [_pt(i * 10, 50, -50, bssid="ap-b") for i in range(5)]
        findings = compute_insights(points, 100, 100)
        underused = [f for f in findings if "Underused" in f["title"]]
        self.assertEqual(len(underused), 0)


class TestSurveyQuality(unittest.TestCase):
    def test_clustered_points_warn(self):
        """All points in one corner -> low grid coverage -> warn."""
        points = [_pt(5, 5, -50) for _ in range(5)]
        findings = compute_insights(points, 400, 400)
        quality = [f for f in findings if f["category"] == "quality"]
        self.assertTrue(len(quality) > 0)
        self.assertEqual(quality[0]["severity"], "warn")
        self.assertIn("Add measurements", quality[0]["body"])

    def test_spread_points_info(self):
        """Points in every quadrant -> good coverage."""
        points = []
        for row in range(4):
            for col in range(4):
                points.append(_pt(col * 100 + 50, row * 100 + 50, -50))
        findings = compute_insights(points, 400, 400)
        quality = [f for f in findings if f["category"] == "quality"]
        self.assertTrue(len(quality) > 0)
        self.assertEqual(quality[0]["severity"], "info")


class TestDeadZones(unittest.TestCase):
    def test_locked_below_threshold(self):
        """<15 points should produce a locked finding."""
        points = [_pt(i * 10, 10, -50) for i in range(5)]
        findings = compute_insights(points, 100, 100)
        dz = [f for f in findings if f["category"] == "dead-zone"]
        self.assertTrue(len(dz) > 0)
        self.assertTrue(dz[0].get("locked"))

    def test_dead_zone_detected(self):
        """Strong points in one spot + weak elsewhere -> dead zone found."""
        points = []
        # 10 strong points at top-left
        for i in range(10):
            points.append(_pt(10 + i, 10 + i, -35))
        # 5 very weak points spread around
        for i in range(5):
            points.append(_pt(80 + i, 80 + i, -82))
        findings = compute_insights(points, 100, 100)
        dz = [f for f in findings if f["category"] == "dead-zone" and not f.get("locked")]
        self.assertTrue(len(dz) > 0)
        self.assertIn("dead zone", dz[0]["title"].lower())
        self.assertGreater(dz[0]["metric"], 0)


class TestCompareSurveys(unittest.TestCase):
    def test_point_count_diff(self):
        current = [_pt(10, 10, -50) for _ in range(10)]
        snapshot = [_pt(10, 10, -50) for _ in range(5)]
        diffs = compare_surveys(current, snapshot, 100, 100)
        count_diff = [d for d in diffs if d["category"] == "comparison" and "count" in d["title"].lower()]
        self.assertTrue(len(count_diff) > 0)
        self.assertEqual(count_diff[0]["metric"], 5)

    def test_rssi_improvement(self):
        current = [_pt(10, 10, -40), _pt(50, 50, -42), _pt(90, 90, -38)]
        snapshot = [_pt(10, 10, -55), _pt(50, 50, -60), _pt(90, 90, -58)]
        diffs = compare_surveys(current, snapshot, 100, 100)
        avg_diff = [d for d in diffs if "signal" in d["title"].lower()]
        self.assertTrue(len(avg_diff) > 0)
        self.assertGreater(avg_diff[0]["metric"], 0)
        self.assertIn("improved", avg_diff[0]["body"])


class TestTooFewPoints(unittest.TestCase):
    def test_returns_empty_below_3(self):
        points = [_pt(10, 10, -50), _pt(20, 20, -55)]
        findings = compute_insights(points, 100, 100)
        self.assertEqual(findings, [])


class TestWeakPoints(unittest.TestCase):
    def test_flags_individual_weak_points(self):
        points = [
            _pt(10, 10, -40),
            _pt(50, 50, -72),  # below -70
            _pt(90, 90, -50),
        ]
        findings = compute_insights(points, 100, 100)
        weak = [f for f in findings if f["category"] == "weak-point"]
        self.assertEqual(len(weak), 1)
        self.assertEqual(weak[0]["metric"], -72)

    def test_no_flag_when_all_strong(self):
        points = [_pt(i * 30, 10, -45) for i in range(3)]
        findings = compute_insights(points, 100, 100)
        weak = [f for f in findings if f["category"] == "weak-point"]
        self.assertEqual(len(weak), 0)


class TestLowTxRate(unittest.TestCase):
    def test_flags_low_tx_despite_ok_rssi(self):
        points = [
            _pt(10, 10, -50, tx_rate=500.0),
            _pt(50, 50, -55, tx_rate=50.0),  # RSSI OK but Tx terrible
            _pt(90, 90, -50, tx_rate=400.0),
        ]
        findings = compute_insights(points, 100, 100)
        low = [f for f in findings if f["category"] == "throughput"]
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["metric"], 50.0)

    def test_no_flag_when_rssi_also_bad(self):
        """Don't double-flag: if RSSI < -70 AND Tx low, weak-point covers it."""
        points = [
            _pt(10, 10, -50, tx_rate=500.0),
            _pt(50, 50, -75, tx_rate=30.0),  # both bad — only weak-point should flag
            _pt(90, 90, -50, tx_rate=400.0),
        ]
        findings = compute_insights(points, 100, 100)
        low = [f for f in findings if f["category"] == "throughput"]
        self.assertEqual(len(low), 0)


class TestStickyClient(unittest.TestCase):
    def test_warns_when_aps_exceed_bssids(self):
        points = [_pt(i * 10, 10, -50, bssid="aa:aa") for i in range(5)]
        aps = [{"id": "1"}, {"id": "2"}, {"id": "3"}]  # 3 APs but only 1 BSSID
        findings = compute_insights(points, 100, 100, access_points=aps)
        sticky = [f for f in findings if f["category"] == "roaming"]
        self.assertEqual(len(sticky), 1)
        self.assertIn("sticky", sticky[0]["title"].lower())

    def test_no_warn_with_single_ap(self):
        points = [_pt(i * 10, 10, -50) for i in range(3)]
        aps = [{"id": "1"}]
        findings = compute_insights(points, 100, 100, access_points=aps)
        sticky = [f for f in findings if f["category"] == "roaming"]
        self.assertEqual(len(sticky), 0)


class TestRoomInsights(unittest.TestCase):
    def test_per_room_summary(self):
        rooms = [_room("Kitchen", 0, 0, 50, 50)]
        points = [
            _pt(10, 10, -55),
            _pt(30, 30, -65),
            _pt(80, 80, -45),  # outside Kitchen
        ]
        findings = compute_insights(points, 100, 100, rooms=rooms)
        room_f = [f for f in findings if f["category"] == "room"]
        self.assertEqual(len(room_f), 1)
        self.assertEqual(room_f[0]["title"], "Kitchen")
        self.assertEqual(room_f[0]["metric"], -60)  # avg of -55 and -65

    def test_extremes_use_room_name(self):
        rooms = [_room("Office", 0, 0, 50, 50)]
        points = [
            _pt(10, 10, -80),  # worst, inside Office
            _pt(80, 80, -40),
            _pt(60, 60, -50),
        ]
        findings = compute_insights(points, 100, 100, rooms=rooms)
        weakest = next(f for f in findings if f["title"] == "Weakest measurement")
        self.assertIn("Office", weakest["body"])


if __name__ == "__main__":
    unittest.main()
