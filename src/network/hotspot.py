"""Wi-Fi AP fallback via NetworkManager.

When the Pi has no upstream Wi-Fi, ``inkypi-netd.service`` brings up a
self-hosted access point so the user can join with their phone and provision
real credentials (or just use InkyPi over the local AP).

We use NetworkManager's built-in AP mode rather than ``hostapd`` directly —
fewer moving parts, no config templates to maintain, and it cooperates with
the rest of NM (so a successful ``nmcli dev wifi connect`` while in AP mode
automatically tears the AP down).
"""

from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from typing import Optional

from network._nm import NmcliError, run, terse
from network.wifi import WIFI_IFACE

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "InkyPi-AP"
DEFAULT_IPV4_GATEWAY = "192.168.4.1"
DEFAULT_IPV4_CIDR = f"{DEFAULT_IPV4_GATEWAY}/24"


@dataclass
class HotspotConfig:
    ssid: str
    password: str
    profile: str = DEFAULT_PROFILE_NAME
    gateway: str = DEFAULT_IPV4_GATEWAY

    @classmethod
    def generate(cls, hostname: str) -> "HotspotConfig":
        """Build a sensible default for a fresh install."""
        suffix = hostname.split("-")[-1] if "-" in hostname else hostname
        suffix = "".join(c for c in suffix if c.isalnum())[-4:].upper() or "INKY"
        ssid = f"InkyPi-{suffix}"
        # 12-char alphanumeric password — readable, still 71 bits of entropy.
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(12))
        return cls(ssid=ssid, password=password)


def ensure_profile(cfg: HotspotConfig) -> None:
    """Idempotently create / update the NetworkManager AP profile."""
    exists = _profile_exists(cfg.profile)

    if not exists:
        logger.info("Creating hotspot profile %s (SSID=%s)", cfg.profile, cfg.ssid)
        run([
            "con", "add",
            "type", "wifi",
            "ifname", WIFI_IFACE,
            "con-name", cfg.profile,
            "autoconnect", "no",
            "ssid", cfg.ssid,
        ])

    # Always re-apply settings so the profile reflects current config (e.g.
    # if the user rotated the password).
    run([
        "con", "modify", cfg.profile,
        "802-11-wireless.mode", "ap",
        "802-11-wireless.band", "bg",
        "ipv4.method", "shared",
        "ipv4.addresses", DEFAULT_IPV4_CIDR,
        "ipv6.method", "ignore",
        "802-11-wireless-security.key-mgmt", "wpa-psk",
        "802-11-wireless-security.psk", cfg.password,
    ])


def start(cfg: HotspotConfig) -> None:
    """Activate the AP profile."""
    ensure_profile(cfg)
    if is_active(cfg.profile):
        logger.debug("Hotspot %s already active", cfg.profile)
        return
    logger.info("Starting hotspot %s", cfg.profile)
    run(["con", "up", cfg.profile], timeout=30)


def stop(cfg: Optional[HotspotConfig] = None, profile: str = DEFAULT_PROFILE_NAME) -> None:
    """Deactivate the AP profile if it is up. Safe to call when not active."""
    name = cfg.profile if cfg else profile
    if not is_active(name):
        return
    logger.info("Stopping hotspot %s", name)
    run(["con", "down", name], check=False, timeout=15)


def is_active(profile: str = DEFAULT_PROFILE_NAME) -> bool:
    rows = terse(
        ["-g", "GENERAL.STATE", "con", "show", profile],
        fields=[],
        check=False,
    )
    state = (rows[0][0] if rows and rows[0] else "").strip()
    return state == "activated"


def _profile_exists(name: str) -> bool:
    try:
        rows = terse(["con", "show"], fields=["NAME"])
    except NmcliError:
        return False
    return any(row and row[0] == name for row in rows)
