"""Wi-Fi client operations: scan, connect, forget, status.

These are the building blocks consumed by both the BLE provisioning handler
and the connectivity monitor.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

from network._nm import NmcliError, run, terse

logger = logging.getLogger(__name__)

WIFI_IFACE = "wlan0"


@dataclass
class WifiNetwork:
    ssid: str
    signal: int           # dBm-ish (nmcli reports 0-100 percentage; we report raw)
    security: str         # "OPEN", "WEP", "WPA1", "WPA2", "WPA3", or compound
    in_use: bool = False


@dataclass
class WifiStatus:
    mode: str             # "client" | "ap" | "offline"
    ssid: Optional[str]
    ip: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


def scan(*, rescan: bool = True) -> list[WifiNetwork]:
    """Return all currently visible Wi-Fi networks, strongest first."""
    if rescan:
        # ``rescan auto`` is honoured by the next ``list`` invocation.
        try:
            run(["dev", "wifi", "rescan"], check=False, timeout=15)
        except NmcliError as exc:
            logger.warning("Wi-Fi rescan failed (continuing with cached list): %s", exc)

    rows = terse(
        ["dev", "wifi", "list", "--rescan", "no"],
        fields=["IN-USE", "SSID", "SIGNAL", "SECURITY"],
    )

    networks: list[WifiNetwork] = []
    seen_ssids: set[str] = set()
    for row in rows:
        if len(row) < 4:
            continue
        in_use_raw, ssid, signal_raw, security = row[0], row[1], row[2], row[3]
        if not ssid or ssid in seen_ssids:
            continue
        seen_ssids.add(ssid)
        try:
            signal = int(signal_raw)
        except ValueError:
            signal = 0
        networks.append(
            WifiNetwork(
                ssid=ssid,
                signal=signal,
                security=(security or "OPEN").strip(),
                in_use=in_use_raw.strip() == "*",
            )
        )
    networks.sort(key=lambda n: n.signal, reverse=True)
    return networks


def connect(ssid: str, password: Optional[str] = None) -> WifiStatus:
    """Join the given network. Creates / overwrites a NetworkManager profile."""
    if not ssid:
        raise ValueError("ssid is required")

    args = ["dev", "wifi", "connect", ssid, "ifname", WIFI_IFACE]
    if password:
        args += ["password", password]

    # nmcli can take a while to associate; allow up to 45s.
    run(args, timeout=45)
    status = current_status()
    if status.mode != "client" or status.ssid != ssid:
        raise NmcliError(f"connected but unexpected state: {status}")
    return status


def forget(ssid: str) -> None:
    """Delete the saved NetworkManager profile for ``ssid``."""
    if not ssid:
        raise ValueError("ssid is required")
    run(["con", "delete", ssid], check=False)


def list_saved() -> list[str]:
    """Return SSIDs of every saved Wi-Fi connection profile.

    NetworkManager stores profile NAMEs that for wifi-type connections
    default to the SSID (matching what ``connect`` produced). The AP
    fallback profile (``InkyPi-AP``) is excluded since the user manages
    it elsewhere.
    """
    rows = terse(["con", "show"], fields=["NAME", "TYPE"], check=False)
    ssids: list[str] = []
    for row in rows:
        if len(row) < 2:
            continue
        name, conn_type = row[0], row[1]
        if conn_type != "802-11-wireless":
            continue
        if name == "InkyPi-AP":
            continue
        ssids.append(name)
    return ssids


def current_status() -> WifiStatus:
    """Probe NetworkManager for the current Wi-Fi state on ``wlan0``."""
    rows = terse(
        ["-g", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", WIFI_IFACE],
        fields=[],
        check=False,
    )
    # ``-g`` already returns one value per line; flatten.
    values = [v for row in rows for v in row]
    # Pad to length 3 to make indexing safe.
    values += [""] * max(0, 3 - len(values))
    raw_state, connection_name, ip_with_mask = values[0], values[1], values[2]

    ip = ip_with_mask.split("/", 1)[0] if ip_with_mask else None

    if not connection_name or connection_name == "--":
        return WifiStatus(mode="offline", ssid=None, ip=ip or None)

    mode = "ap" if _is_ap_profile(connection_name) else "client"
    ssid = _profile_ssid(connection_name) or connection_name
    return WifiStatus(mode=mode, ssid=ssid, ip=ip)


def _is_ap_profile(connection_name: str) -> bool:
    rows = terse(
        ["-g", "802-11-wireless.mode", "con", "show", connection_name],
        fields=[],
        check=False,
    )
    value = (rows[0][0] if rows and rows[0] else "").strip()
    return value == "ap"


def _profile_ssid(connection_name: str) -> Optional[str]:
    rows = terse(
        ["-g", "802-11-wireless.ssid", "con", "show", connection_name],
        fields=[],
        check=False,
    )
    return (rows[0][0] if rows and rows[0] else "").strip() or None
