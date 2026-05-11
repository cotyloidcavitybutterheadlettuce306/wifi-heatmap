"""
WiFi scanner for macOS.

Wraps `sudo wdutil info` and parses the WIFI section to extract
the currently connected network's signal data.

Why wdutil?
- The classic `airport` binary was removed in macOS 14.4+
- `system_profiler SPAirPortDataType` redacts BSSIDs on modern macOS
- `ioreg` returns all-zero BSSIDs (Apple privacy lockdown)
- `wdutil info` is the only reliable way to get full data, but needs sudo

Usage:
    scanner = WifiScanner(sudo_password="...")
    sample = scanner.scan()
    # -> {'ssid': 'Home', 'bssid': 'aa:bb:...', 'rssi': -45, ...}
"""
from __future__ import annotations

import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, asdict
from typing import Optional, Protocol, runtime_checkable


# Fields we care about from the WIFI section of `wdutil info`.
# Mapping wdutil-key -> our internal name. We coerce types in parse_wifi_block.
WIFI_FIELDS = {
    "SSID": "ssid",
    "BSSID": "bssid",
    "RSSI": "rssi",            # int (dBm)
    "Noise": "noise",          # int (dBm)
    "Channel": "channel",      # str like "5g40/80"
    "PHY Mode": "phy_mode",    # str like "11ac"
    "Tx Rate": "tx_rate",      # float (Mbps)
    "Security": "security",
    "MCS Index": "mcs_index",  # int
}


@dataclass
class WifiSample:
    """A single WiFi measurement at a point in time."""
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    rssi: Optional[int] = None
    noise: Optional[int] = None
    channel: Optional[str] = None
    phy_mode: Optional[str] = None
    tx_rate: Optional[float] = None
    security: Optional[str] = None
    mcs_index: Optional[int] = None

    def to_dict(self):
        return asdict(self)

    @property
    def is_valid(self) -> bool:
        """A sample is valid if we got at least an RSSI reading."""
        return self.rssi is not None


class ScanError(Exception):
    """Raised when wdutil fails or its output can't be parsed."""


# ── Scanner Protocol ────────────────────────────────────────────

@runtime_checkable
class Scanner(Protocol):
    """Contract for all scanner implementations."""

    def scan(self) -> WifiSample: ...
    def verify_credentials(self) -> bool: ...
    def force_reconnect(self) -> None: ...


# ── Speed test ──────────────────────────────────────────────────

SPEED_TEST_URL = "https://speed.cloudflare.com/__down?bytes=100000000"  # 100 MB
SPEED_TEST_TIMEOUT = 60


def speed_test(url: str = SPEED_TEST_URL, timeout: float = SPEED_TEST_TIMEOUT) -> float:
    """Download a test file and return speed in Mbps."""
    start = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "wifi-heatmap"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    elapsed = time.time() - start

    if elapsed <= 0:
        return 0.0
    return round((len(data) * 8) / (elapsed * 1_000_000), 1)


# ── Local scanner ───────────────────────────────────────────────

class WifiScanner:
    """Wraps `sudo wdutil info` for repeated scanning."""

    def __init__(self, sudo_password: str, timeout: float = 10.0):
        self.sudo_password = sudo_password
        self.timeout = timeout

    def _run_wdutil(self) -> str:
        """Execute `sudo -S wdutil info` and return stdout text."""
        try:
            # -S reads password from stdin; -p '' suppresses the password prompt
            result = subprocess.run(
                ["sudo", "-S", "-p", "", "wdutil", "info"],
                input=self.sudo_password + "\n",
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise ScanError(f"wdutil timed out after {self.timeout}s")
        except FileNotFoundError:
            raise ScanError(
                "wdutil not found. This tool only works on macOS."
            )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "incorrect password" in stderr.lower() or "Sorry" in stderr:
                raise ScanError("Incorrect sudo password")
            raise ScanError(f"wdutil failed: {stderr or 'unknown error'}")

        return result.stdout

    def scan(self) -> WifiSample:
        """Run a single scan and return the parsed sample."""
        output = self._run_wdutil()
        return parse_wdutil_output(output)

    def force_reconnect(self) -> None:
        """Toggle WiFi off/on to force the laptop to pick the nearest AP."""
        try:
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "off"],
                capture_output=True, text=True, timeout=5,
            )
            time.sleep(1)
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "on"],
                capture_output=True, text=True, timeout=5,
            )
            time.sleep(3)
        except Exception:
            raise ScanError("Failed to toggle WiFi for force-reconnect")

    def verify_credentials(self) -> bool:
        """Try a scan to verify the sudo password works. Raises ScanError if not."""
        sample = self.scan()
        if not sample.is_valid:
            raise ScanError(
                "wdutil ran but no RSSI was found - is WiFi connected?"
            )
        return True


# ── Remote scanner ──────────────────────────────────────────────

class RemoteWifiScanner:
    """Fetches WiFi data from a remote agent instead of local wdutil."""

    def __init__(self, agent_url: str, timeout: float = 10.0):
        url = agent_url.strip().rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"
        if url.count(":") < 2:  # no port specified
            url = f"{url}:5555"
        self.agent_url = url
        self.timeout = timeout

    def scan(self) -> WifiSample:
        import json
        url = f"{self.agent_url}/scan"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise ScanError(f"Remote agent error: {e}")

        if "error" in data:
            raise ScanError(data["error"])

        return WifiSample(
            ssid=data.get("ssid"),
            bssid=data.get("bssid"),
            rssi=data.get("rssi"),
            noise=data.get("noise"),
            channel=data.get("channel"),
            phy_mode=data.get("phy_mode"),
            tx_rate=data.get("tx_rate"),
            security=data.get("security"),
            mcs_index=data.get("mcs_index"),
        )

    def verify_credentials(self) -> bool:
        sample = self.scan()
        if not sample.is_valid:
            raise ScanError("Remote agent returned no RSSI")
        return True

    def force_reconnect(self) -> None:
        pass  # not supported on remote — agent stays connected


# ── Parsing ─────────────────────────────────────────────────────

def parse_wdutil_output(text: str) -> WifiSample:
    """
    Parse the full `wdutil info` output, extract the WIFI section,
    and return a WifiSample with whatever fields we found.
    """
    wifi_block = extract_wifi_block(text)
    if not wifi_block:
        raise ScanError("Could not find WIFI section in wdutil output")
    return parse_wifi_block(wifi_block)


def extract_wifi_block(text: str) -> str:
    """
    Pull out just the WIFI section between the dashes.

    wdutil output looks like:
        ————...————
        NETWORK
        ————...————
            ...network fields...
        ————...————
        WIFI
        ————...————
            ...wifi fields...
        ————...————
        BLUETOOTH
        ...

    We find "WIFI" on its own line, then capture lines until the next
    section divider (a line of em-dashes) followed by an ALL-CAPS header.
    """
    lines = text.splitlines()

    # Find the WIFI header
    wifi_start = None
    for i, line in enumerate(lines):
        if line.strip() == "WIFI":
            wifi_start = i
            break

    if wifi_start is None:
        return ""

    # Skip past the closing divider after WIFI header
    # (lines[wifi_start+1] should be the dashes line)
    content_start = wifi_start + 2

    # Collect lines until we hit the next section.
    # A new section is signaled by: a divider line, then an ALL-CAPS header.
    collected = []
    i = content_start
    while i < len(lines):
        line = lines[i]
        # Check if this is a divider followed by a section header
        if _is_divider(line) and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            if next_line and next_line.isupper() and next_line != "WIFI":
                break
        collected.append(line)
        i += 1

    return "\n".join(collected)


def _is_divider(line: str) -> bool:
    """True if line is a row of em-dashes used as section divider."""
    stripped = line.strip()
    if len(stripped) < 10:
        return False
    # The dividers in wdutil output are em-dashes (U+2014)
    return all(c == "\u2014" for c in stripped)


def parse_wifi_block(block: str) -> WifiSample:
    """
    Parse a `key : value` block (the WIFI section) into a WifiSample.

    Each line looks like:
        '    RSSI                 : -44 dBm'
        '    SSID                 : MyNetwork'
        '    Tx Rate              : 526.0 Mbps'

    Some values have units we strip; some are continuation lines
    (indented further, no key) which we ignore.
    """
    sample = WifiSample()

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue

        # Continuation lines start with the value directly (no key).
        # These look like:  "                         : 192.168.68.1"
        # Skip them.
        if line.startswith(":"):
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if key not in WIFI_FIELDS:
            continue

        attr = WIFI_FIELDS[key]
        coerced = _coerce_value(attr, value)
        if coerced is not None:
            setattr(sample, attr, coerced)

    return sample


def _coerce_value(attr: str, raw: str):
    """Convert raw string value into the right type, stripping units."""
    if not raw:
        return None

    if attr == "rssi" or attr == "noise":
        # "-44 dBm" -> -44
        m = re.search(r"-?\d+", raw)
        return int(m.group()) if m else None

    if attr == "tx_rate":
        # "526.0 Mbps" -> 526.0
        m = re.search(r"-?\d+(?:\.\d+)?", raw)
        return float(m.group()) if m else None

    if attr == "mcs_index":
        m = re.search(r"\d+", raw)
        return int(m.group()) if m else None

    # Everything else stays as a string
    return raw


# CLI for testing the scanner standalone
if __name__ == "__main__":
    import getpass
    import json
    import sys

    print("WiFi Scanner Test")
    print("-" * 40)
    pw = getpass.getpass("Enter your sudo password: ")

    scanner = WifiScanner(sudo_password=pw)
    try:
        sample = scanner.scan()
    except ScanError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nParsed WiFi sample:")
    print(json.dumps(sample.to_dict(), indent=2))

    if sample.is_valid:
        print(f"\n✓ Got valid RSSI: {sample.rssi} dBm")
    else:
        print("\n✗ No RSSI captured - check that WiFi is connected")
        sys.exit(1)
