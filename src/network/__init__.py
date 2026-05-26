"""Networking helpers for InkyPi: Wi-Fi client management, AP fallback,
and a connectivity monitor that switches between the two.

All operations shell out to ``nmcli`` (NetworkManager) and therefore require
Raspberry Pi OS Bookworm or newer, where NetworkManager is the default
network stack. On older releases (Bullseye), install NetworkManager manually
or use the ``hostapd``-based legacy path documented in ``docs/bluetooth.md``.
"""

from network.wifi import (
    WifiNetwork,
    WifiStatus,
    connect,
    current_status,
    forget,
    scan,
)
from network.hotspot import HotspotConfig, ensure_profile, start, stop
from network.monitor import ConnectivityMonitor

__all__ = [
    "ConnectivityMonitor",
    "HotspotConfig",
    "WifiNetwork",
    "WifiStatus",
    "connect",
    "current_status",
    "ensure_profile",
    "forget",
    "scan",
    "start",
    "stop",
]
