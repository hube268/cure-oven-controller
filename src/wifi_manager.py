"""
wifi_manager.py — WiFi Management via nmcli

Uses NetworkManager (standard on Raspberry Pi OS) to:
  - List visible SSIDs
  - Connect to a network with a password
  - Report current connection status
"""

import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _run(cmd: list, timeout: int = 15) -> tuple[bool, str]:
    """Run a shell command; return (success, output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "nmcli not found — is NetworkManager installed?"
    except Exception as exc:
        return False, str(exc)


def scan_networks() -> list[dict]:
    """
    Return a list of visible WiFi networks.
    Each entry: {ssid, signal, security, in_use}
    """
    ok, out = _run([
        "nmcli", "-t", "-f",
        "SSID,SIGNAL,SECURITY,IN-USE",
        "device", "wifi", "list"
    ])
    if not ok or not out:
        logger.warning("WiFi scan failed: %s", out)
        return []

    networks = []
    seen = set()
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        networks.append({
            "ssid":     ssid,
            "signal":   _safe_int(parts[1]),
            "security": parts[2] or "Open",
            "in_use":   parts[3].strip() == "*",
        })

    # Sort: connected first, then by signal strength
    networks.sort(key=lambda n: (not n["in_use"], -n["signal"]))
    return networks


def connect(ssid: str, password: Optional[str] = None) -> tuple[bool, str]:
    """
    Connect to a WiFi network.
    Returns (success, message).
    """
    if password:
        ok, out = _run([
            "nmcli", "device", "wifi", "connect", ssid,
            "password", password,
        ], timeout=30)
    else:
        ok, out = _run([
            "nmcli", "device", "wifi", "connect", ssid,
        ], timeout=30)

    if ok:
        logger.info("Connected to WiFi: %s", ssid)
        return True, f"Connected to {ssid}"
    else:
        logger.warning("WiFi connect failed: %s", out)
        return False, out


def disconnect() -> tuple[bool, str]:
    """Disconnect the active WiFi connection."""
    ok, out = _run(["nmcli", "device", "disconnect", "wlan0"])
    return ok, out


def current_connection() -> dict:
    """Return info about the active WiFi connection."""
    ok, out = _run([
        "nmcli", "-t", "-f",
        "GENERAL.DEVICE,GENERAL.CONNECTION,IP4.ADDRESS",
        "device", "show", "wlan0"
    ])
    if not ok:
        return {"connected": False, "ssid": None, "ip": None}

    info: dict = {"connected": False, "ssid": None, "ip": None}
    for line in out.splitlines():
        if "GENERAL.CONNECTION" in line:
            val = line.split(":", 1)[-1].strip()
            if val and val != "--":
                info["ssid"] = val
                info["connected"] = True
        elif "IP4.ADDRESS" in line:
            val = line.split(":", 1)[-1].strip()
            if val and val != "--":
                info["ip"] = val.split("/")[0]

    return info


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
