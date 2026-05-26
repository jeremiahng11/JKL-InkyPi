#!/usr/bin/env python3
"""Entry point for ``inkypi-netd.service`` — the connectivity monitor that
fails over to an InkyPi-hosted Wi-Fi access point whenever the upstream
connection is unavailable.

Reads / persists hotspot credentials in ``device.json`` so the BLE service
and the web UI can surface them to the user.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import socket
import sys

from network.hotspot import HotspotConfig
from network.monitor import ConnectivityMonitor

CONFIG_FILE = os.environ.get(
    "INKYPI_CONFIG_FILE",
    os.path.join(os.path.dirname(__file__), "config", "device.json"),
)
LOGGING_CONF = os.path.join(os.path.dirname(__file__), "config", "logging.conf")

if os.path.exists(LOGGING_CONF):
    logging.config.fileConfig(LOGGING_CONF, disable_existing_loggers=False)
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger("inkypi-netd")


def _load_or_init_hotspot_config() -> HotspotConfig:
    """Read hotspot creds from device.json, generating them on first run."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.warning("device.json not found at %s, using ephemeral hotspot config", CONFIG_FILE)
        return HotspotConfig.generate(socket.gethostname())

    raw = config.get("hotspot") or {}
    if raw.get("ssid") and raw.get("password"):
        return HotspotConfig(
            ssid=raw["ssid"],
            password=raw["password"],
            profile=raw.get("profile", "InkyPi-AP"),
            gateway=raw.get("gateway", "192.168.4.1"),
        )

    cfg = HotspotConfig.generate(socket.gethostname())
    config["hotspot"] = {
        "ssid": cfg.ssid,
        "password": cfg.password,
        "profile": cfg.profile,
        "gateway": cfg.gateway,
    }
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Generated hotspot credentials (SSID=%s) and wrote to %s", cfg.ssid, CONFIG_FILE)
    except OSError:
        logger.exception("Failed to persist hotspot credentials — continuing with in-memory config")
    return cfg


def main() -> int:
    cfg = _load_or_init_hotspot_config()
    monitor = ConnectivityMonitor(cfg)
    monitor.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
