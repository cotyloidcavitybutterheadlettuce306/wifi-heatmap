"""Tests for wdutil output parsing — the most fragile logic in the project."""
import unittest
from scanner import (
    parse_wdutil_output, extract_wifi_block, parse_wifi_block,
    _coerce_value, WifiSample, ScanError,
)

# Realistic wdutil output (em-dash dividers, indented key:value pairs)
SAMPLE_WDUTIL_OUTPUT = """\
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
NETWORK
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
    Interface Name       : en0
    Power                : On
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
WIFI
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
    SSID                 : HomeNetwork
    BSSID                : aa:bb:cc:dd:ee:ff
    RSSI                 : -44 dBm
    Noise                : -94 dBm
    Tx Rate              : 526.0 Mbps
    Security             : WPA2 Personal
    PHY Mode             : 11ac
    Channel              : 5g40/80
    MCS Index            : 7
    NSS                  : 2
    Guard Interval       : 800
                         : 192.168.1.1
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
BLUETOOTH
\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014\u2014
    Address              : 00:11:22:33:44:55
"""


class TestParseFullOutput(unittest.TestCase):
    def test_all_fields_parsed(self):
        sample = parse_wdutil_output(SAMPLE_WDUTIL_OUTPUT)
        self.assertEqual(sample.ssid, "HomeNetwork")
        self.assertEqual(sample.bssid, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(sample.rssi, -44)
        self.assertEqual(sample.noise, -94)
        self.assertEqual(sample.tx_rate, 526.0)
        self.assertEqual(sample.security, "WPA2 Personal")
        self.assertEqual(sample.phy_mode, "11ac")
        self.assertEqual(sample.channel, "5g40/80")
        self.assertEqual(sample.mcs_index, 7)

    def test_is_valid_with_rssi(self):
        sample = parse_wdutil_output(SAMPLE_WDUTIL_OUTPUT)
        self.assertTrue(sample.is_valid)

    def test_no_wifi_section_raises(self):
        with self.assertRaises(ScanError):
            parse_wdutil_output("just some random text\nwith no wifi section")


class TestExtractWifiBlock(unittest.TestCase):
    def test_returns_empty_when_no_wifi_header(self):
        self.assertEqual(extract_wifi_block("no headers here"), "")

    def test_stops_at_next_section(self):
        block = extract_wifi_block(SAMPLE_WDUTIL_OUTPUT)
        self.assertIn("RSSI", block)
        self.assertNotIn("BLUETOOTH", block)
        self.assertNotIn("NETWORK", block)

    def test_includes_continuation_lines(self):
        """Continuation lines (like ': 192.168.1.1') should be in the block."""
        block = extract_wifi_block(SAMPLE_WDUTIL_OUTPUT)
        self.assertIn("192.168.1.1", block)

    def test_wifi_as_last_section(self):
        """WIFI section at end of output (no BLUETOOTH after it)."""
        output = SAMPLE_WDUTIL_OUTPUT.split("BLUETOOTH")[0].rstrip()
        block = extract_wifi_block(output)
        self.assertIn("RSSI", block)


class TestParseWifiBlock(unittest.TestCase):
    def test_partial_data(self):
        """Only some fields present — rest should be None, no crash."""
        block = "    RSSI                 : -55 dBm\n    SSID                 : Test"
        sample = parse_wifi_block(block)
        self.assertEqual(sample.rssi, -55)
        self.assertEqual(sample.ssid, "Test")
        self.assertIsNone(sample.bssid)
        self.assertIsNone(sample.tx_rate)

    def test_empty_block(self):
        sample = parse_wifi_block("")
        self.assertIsNone(sample.rssi)
        self.assertFalse(sample.is_valid)

    def test_continuation_lines_skipped(self):
        """Lines starting with ':' (no key) should not crash or overwrite."""
        block = (
            "    SSID                 : MyNet\n"
            "                         : 192.168.1.1\n"
            "    RSSI                 : -50 dBm\n"
        )
        sample = parse_wifi_block(block)
        self.assertEqual(sample.ssid, "MyNet")
        self.assertEqual(sample.rssi, -50)

    def test_unknown_fields_ignored(self):
        block = "    SomeNewField         : whatever\n    RSSI                 : -60 dBm"
        sample = parse_wifi_block(block)
        self.assertEqual(sample.rssi, -60)


class TestCoerceValue(unittest.TestCase):
    def test_rssi_with_unit(self):
        self.assertEqual(_coerce_value("rssi", "-44 dBm"), -44)

    def test_rssi_without_unit(self):
        self.assertEqual(_coerce_value("rssi", "-44"), -44)

    def test_noise_positive_edge(self):
        self.assertEqual(_coerce_value("noise", "0 dBm"), 0)

    def test_tx_rate_float(self):
        self.assertAlmostEqual(_coerce_value("tx_rate", "866.7 Mbps"), 866.7)

    def test_mcs_index(self):
        self.assertEqual(_coerce_value("mcs_index", "9"), 9)

    def test_empty_string_returns_none(self):
        self.assertIsNone(_coerce_value("rssi", ""))

    def test_no_digits_returns_none(self):
        self.assertIsNone(_coerce_value("rssi", "no number here"))

    def test_string_field_passthrough(self):
        self.assertEqual(_coerce_value("ssid", "My Network"), "My Network")


class TestWifiSampleRoundtrip(unittest.TestCase):
    def test_to_dict_from_dict(self):
        original = WifiSample(ssid="Net", bssid="aa:bb", rssi=-50, noise=-90,
                              channel="5g40", tx_rate=300.0)
        rebuilt = WifiSample(**original.to_dict())
        self.assertEqual(rebuilt.rssi, original.rssi)
        self.assertEqual(rebuilt.ssid, original.ssid)
        self.assertEqual(rebuilt.bssid, original.bssid)


if __name__ == "__main__":
    unittest.main()
