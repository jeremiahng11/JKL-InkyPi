"""Builds the JSON payload published on the INFO characteristic."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Optional

from ble.bridge import FlaskBridge
from network import hotspot, wifi

logger = logging.getLogger(__name__)


def build_info(config_file: str, hotspot_cfg: hotspot.HotspotConfig, bridge: FlaskBridge) -> bytes:
    """Snapshot of device state, encoded as a single JSON object.

    Designed to fit inside one fragmented notification (~1KB) so phones can
    read it in one round-trip.
    """
    cfg = _safe_read(config_file)
    wifi_status = _safe_wifi_status()

    payload = {
        "name":            cfg.get("name") or socket.gethostname(),
        "version":         cfg.get("version", "1.0.0"),
        "wifi": {
            "mode":  wifi_status.mode if wifi_status else "offline",
            "ssid":  wifi_status.ssid if wifi_status else None,
            "ip":    wifi_status.ip if wifi_status else None,
        },
        "ap": {
            "ssid":     hotspot_cfg.ssid,
            "password": hotspot_cfg.password,
            "ip":       hotspot_cfg.gateway,
        },
        "display": {
            "resolution":             cfg.get("resolution"),
            "current_plugin":         cfg.get("refresh_info", {}).get("plugin_id"),
            "last_refresh":           cfg.get("refresh_info", {}).get("refresh_time"),
            "cycle_interval_seconds": cfg.get("plugin_cycle_interval_seconds"),
        },
        "flask_reachable": bridge.reachable(),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _safe_read(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read device config at %s", path)
        return {}


def _safe_wifi_status() -> Optional[wifi.WifiStatus]:
    try:
        return wifi.current_status()
    except Exception:
        logger.exception("Failed to read Wi-Fi status")
        return None
