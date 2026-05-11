#!/usr/bin/env python3
"""
WiFi scan agent — runs on a remote machine (macOS, Linux, or Windows).

Zero dependencies, pure stdlib. Copy this single file to the remote machine and run:

    python3 agent.py

It will:
  1. Detect your OS and choose the right WiFi scan method
  2. Ask for sudo password (macOS/Linux only, if needed)
  3. Start a tiny HTTP server on port 5555
  4. Serve WiFi scan results at GET /scan

The main wifi-heatmap app connects to this agent instead of running wdutil locally.
"""
import http.server
import json
import platform
import re
import subprocess
import sys
import getpass

PORT = 5555
PASSWORD = ""
OS = platform.system()  # "Darwin", "Linux", "Windows"


# ── Platform-specific scan implementations ───────────────────────

def scan_macos():
    """Scan using wdutil info (macOS 14.4+). Requires sudo."""
    result = subprocess.run(
        ["sudo", "-S", "-p", "", "wdutil", "info"],
        input=PASSWORD + "\n",
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return None
    return _parse_wdutil(result.stdout)


def scan_linux():
    """Scan using nmcli or iwconfig (Linux)."""
    # Try nmcli first (modern, no root needed)
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f",
             "ACTIVE,SSID,BSSID,SIGNAL,CHAN,FREQ,RATE,SECURITY",
             "dev", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return _parse_nmcli(result.stdout)
    except FileNotFoundError:
        pass

    # Fallback to iwconfig + /proc
    try:
        iw = subprocess.run(
            ["iwconfig"], capture_output=True, text=True, timeout=5,
        )
        if iw.returncode == 0:
            return _parse_iwconfig(iw.stdout)
    except FileNotFoundError:
        pass

    return None


def scan_windows():
    """Scan using netsh wlan show interfaces (Windows)."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return _parse_netsh(result.stdout)
    except FileNotFoundError:
        pass
    return None


# ── Parsers ──────────────────────────────────────────────────────

def _parse_wdutil(text):
    """Parse macOS wdutil info output."""
    fields = {}
    in_wifi = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "WIFI":
            in_wifi = True
            continue
        if in_wifi and len(stripped) > 10 and all(c == "\u2014" for c in stripped):
            if fields:
                break
            continue
        if not in_wifi or ":" not in stripped or stripped.startswith(":"):
            continue
        key, _, val = stripped.partition(":")
        key, val = key.strip(), val.strip()
        if key == "SSID":
            fields["ssid"] = val
        elif key == "BSSID":
            fields["bssid"] = val
        elif key == "RSSI":
            m = re.search(r"-?\d+", val)
            fields["rssi"] = int(m.group()) if m else None
        elif key == "Noise":
            m = re.search(r"-?\d+", val)
            fields["noise"] = int(m.group()) if m else None
        elif key == "Channel":
            fields["channel"] = val
        elif key == "PHY Mode":
            fields["phy_mode"] = val
        elif key == "Tx Rate":
            m = re.search(r"-?\d+(?:\.\d+)?", val)
            fields["tx_rate"] = float(m.group()) if m else None
        elif key == "Security":
            fields["security"] = val
        elif key == "MCS Index":
            m = re.search(r"\d+", val)
            fields["mcs_index"] = int(m.group()) if m else None
    return fields


def _parse_nmcli(text):
    """Parse nmcli -t output. Only returns the active (connected) network."""
    for line in text.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 8:
            continue
        active = parts[0].strip()
        if active.lower() != "yes":
            continue
        # ACTIVE:SSID:BSSID:SIGNAL:CHAN:FREQ:RATE:SECURITY
        signal_pct = int(parts[3]) if parts[3].isdigit() else None
        # Convert signal % to approximate dBm: 100% ≈ -30, 0% ≈ -90
        rssi = round(-30 - (100 - signal_pct) * 0.6) if signal_pct is not None else None
        rate_str = parts[6].replace("Mbit/s", "").strip()
        rate_m = re.search(r"\d+(?:\.\d+)?", rate_str)
        freq = parts[5].strip()
        chan_num = parts[4].strip()
        # Determine band from frequency
        channel = chan_num
        if freq:
            freq_mhz = int(freq) if freq.isdigit() else 0
            if freq_mhz > 4000:
                channel = f"{chan_num} (5 GHz)"
            else:
                channel = f"{chan_num} (2.4 GHz)"
        return {
            "ssid": parts[1].strip() or None,
            "bssid": parts[2].strip().replace("\\:", ":") or None,
            "rssi": rssi,
            "noise": None,
            "channel": channel,
            "phy_mode": None,
            "tx_rate": float(rate_m.group()) if rate_m else None,
            "security": parts[7].strip() or None,
            "mcs_index": None,
        }
    return None


def _parse_iwconfig(text):
    """Parse iwconfig output (basic fallback for Linux)."""
    fields = {}
    for line in text.splitlines():
        if "ESSID:" in line:
            m = re.search(r'ESSID:"([^"]*)"', line)
            if m:
                fields["ssid"] = m.group(1)
        if "Access Point:" in line:
            m = re.search(r"Access Point:\s*([0-9A-Fa-f:]+)", line)
            if m:
                fields["bssid"] = m.group(1)
        if "Signal level" in line:
            m = re.search(r"Signal level[=:]?\s*(-?\d+)", line)
            if m:
                fields["rssi"] = int(m.group(1))
        if "Noise level" in line:
            m = re.search(r"Noise level[=:]?\s*(-?\d+)", line)
            if m:
                fields["noise"] = int(m.group(1))
        if "Bit Rate" in line:
            m = re.search(r"Bit Rate[=:]?\s*(\d+(?:\.\d+)?)", line)
            if m:
                fields["tx_rate"] = float(m.group(1))
        if "Frequency" in line:
            m = re.search(r"Frequency[=:]?\s*([\d.]+)\s*GHz", line)
            if m:
                freq = float(m.group(1))
                fields["channel"] = f"{'5' if freq > 4 else '2.4'} GHz"
    # Fill in missing keys
    for k in ("ssid", "bssid", "rssi", "noise", "channel", "phy_mode",
              "tx_rate", "security", "mcs_index"):
        fields.setdefault(k, None)
    return fields if fields.get("rssi") is not None else None


def _parse_netsh(text):
    """Parse Windows netsh wlan show interfaces output."""
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "ssid" and "bssid" not in key:
            fields["ssid"] = val
        elif key == "bssid":
            fields["bssid"] = val
        elif key == "signal":
            pct = re.search(r"(\d+)", val)
            if pct:
                fields["rssi"] = round(-30 - (100 - int(pct.group(1))) * 0.6)
        elif key == "channel":
            fields["channel"] = val
        elif key in ("radio type", "phy type"):
            fields["phy_mode"] = val
        elif "receive rate" in key or "transmit rate" in key:
            m = re.search(r"(\d+(?:\.\d+)?)", val)
            if m and "tx_rate" not in fields:
                fields["tx_rate"] = float(m.group(1))
        elif key == "authentication":
            fields["security"] = val
    for k in ("ssid", "bssid", "rssi", "noise", "channel", "phy_mode",
              "tx_rate", "security", "mcs_index"):
        fields.setdefault(k, None)
    return fields if fields.get("rssi") is not None or fields.get("ssid") else None


# ── Dispatch ─────────────────────────────────────────────────────

SCANNERS = {
    "Darwin": scan_macos,
    "Linux": scan_linux,
    "Windows": scan_windows,
}


def do_scan():
    fn = SCANNERS.get(OS)
    if fn is None:
        return None
    return fn()


# ── HTTP server ──────────────────────────────────────────────────

class ScanHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/scan":
            self.send_error(404)
            return
        data = do_scan()
        if data is None:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "scan failed"}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        if "/scan" in str(args):
            print(f"  Scan requested from {self.client_address[0]}")


def main():
    global PASSWORD
    print("=" * 44)
    print(f"  WiFi Scan Agent  ({OS})")
    print("=" * 44)

    # macOS needs sudo for wdutil; Linux may need it for iwconfig
    if OS == "Darwin":
        PASSWORD = getpass.getpass("Sudo password: ")
    elif OS == "Linux":
        # nmcli doesn't need root, but iwconfig might
        try:
            subprocess.run(["nmcli", "--version"], capture_output=True, timeout=3)
            print("Using nmcli (no root needed)")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            PASSWORD = getpass.getpass("Sudo password (for iwconfig): ")
    elif OS == "Windows":
        print("Using netsh (no admin needed)")
    else:
        print(f"Unsupported OS: {OS}")
        sys.exit(1)

    # Verify scan works
    print("Verifying scan...", end=" ", flush=True)
    data = do_scan()
    if not data or not data.get("rssi"):
        print("FAILED")
        if not data:
            print("  Could not read WiFi data. Is WiFi connected?")
        else:
            print(f"  Got data but no RSSI: {data}")
        sys.exit(1)

    print(f"OK — {data.get('ssid')} @ {data.get('rssi')} dBm")
    print(f"\nListening on http://0.0.0.0:{PORT}/scan")
    print("Point the wifi-heatmap app to this machine's IP.\n")

    server = http.server.HTTPServer(("0.0.0.0", PORT), ScanHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
