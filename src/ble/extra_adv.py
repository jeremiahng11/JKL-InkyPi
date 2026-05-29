"""Custom BLE advertisement registered alongside bless's default one.

Status file
-----------

This module writes its current state to
``/run/inkypi/extra-adv.status`` after every start() / stop() so the
Flask app can surface "is the secondary advert actually registered?"
in /api/about without snooping the BLE service's process state. One
of ``registered`` / ``skipped`` / ``unknown`` plus the packed mfg
data as hex.


Two reasons for this exists:

1. **Embed the current Wi-Fi IP in the advertisement payload.** The
   companion app reads it from ``ScanResult.advertisementData.
   manufacturerData`` *during BLE scan*, without opening a GATT
   connection. When the Pi is reachable on Wi-Fi the app can probe
   that IP directly and skip the entire 3-5s BLE handshake. mDNS
   covers the same case when multicast works, but multicast is
   blocked on plenty of guest networks and Android Wi-Fi multicast
   permission is finicky — this is the fallback that always works
   if BLE does.

2. **Faster advertising interval** (100-200ms vs the BlueZ default
   ~1280ms). Scan discovery time drops by ~5-6×, so the user sees
   the Pi appear in their scan results within a couple hundred ms
   instead of 1-2 seconds. Small power cost on the Pi.

bless's own advertisement keeps running — this is a *second*
LEAdvertisement1 registered against the same BlueZ
LEAdvertisingManager1 instance. BlueZ supports several concurrent
advertisements per controller.

Manufacturer data layout (under company-id 0xFFFF, the SIG-reserved
testing slot — fine for ad-hoc private use):

    byte 0: version (always 0x01 for now)
    byte 1: wifi mode (0x01=client, 0x02=ap, 0x00=offline)
    bytes 2-5: IPv4 octets

Mirrored on the app side by ``decodeInkyPiAdvertisedAddress`` in
``lib/ble/gatt.dart``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("inkypi-ble.extra_adv")

# Where we write our health status so /api/about can read it without
# IPC into the BLE service. systemd's RuntimeDirectory= drops
# /run/inkypi/ for inkypi.service, but inkypi-ble.service runs as root
# and the path may not exist yet — create it as needed.
STATUS_PATH = "/run/inkypi/extra-adv.status"


def _write_status(state: str, mfg_hex: Optional[str] = None) -> None:
    try:
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with open(STATUS_PATH, "w") as f:
            json.dump({"state": state, "mfg_data": mfg_hex}, f)
    except OSError:
        logger.debug("Could not write %s", STATUS_PATH, exc_info=True)

ADV_PATH = "/org/inkypi/ble/extra_adv"
ADV_IFACE = "org.bluez.LEAdvertisement1"
ADV_MGR_PATH = "/org/bluez/hci0"
ADV_MGR_IFACE = "org.bluez.LEAdvertisingManager1"
BLUEZ_BUS = "org.bluez"

INKYPI_COMPANY_ID = 0xFFFF
INKYPI_ADV_VERSION = 1

# Advertising interval in 0.625ms BLE slots.
#   160 × 0.625ms = 100ms (min)
#   320 × 0.625ms = 200ms (max)
# BlueZ default is roughly 1.28s, so this is 6-13× faster.
MIN_INTERVAL_SLOTS = 160
MAX_INTERVAL_SLOTS = 320


class _ImportError(RuntimeError):
    pass


def _import_dbus():
    try:
        from dbus_fast import Variant  # type: ignore
        from dbus_fast.aio import MessageBus  # type: ignore
        from dbus_fast.constants import BusType, PropertyAccess  # type: ignore
        from dbus_fast.service import ServiceInterface, dbus_property, method  # type: ignore
    except ImportError as exc:
        raise _ImportError(f"dbus-fast unavailable ({exc})") from exc
    return MessageBus, BusType, Variant, ServiceInterface, dbus_property, method, PropertyAccess


def _pack_manufacturer_data(mode: Optional[str], ip: Optional[str]) -> Optional[bytes]:
    """Return the 6-byte payload, or None when the inputs aren't usable."""
    if not ip:
        return None
    try:
        octets = [int(o) for o in ip.split(".")]
    except ValueError:
        return None
    if len(octets) != 4 or any(o < 0 or o > 255 for o in octets):
        return None
    mode_byte = {"client": 0x01, "ap": 0x02}.get(mode or "", 0x00)
    if mode_byte == 0x00:
        return None  # don't advertise an IP we won't be reachable at
    return bytes([INKYPI_ADV_VERSION, mode_byte] + octets)


class ExtraAdvertisement:
    """Lifecycle wrapper around the second LEAdvertisement1 object."""

    def __init__(self, service_uuid: str) -> None:
        self._service_uuid = service_uuid
        self._bus = None
        self._adv = None
        self._registered = False
        self._mode: Optional[str] = None
        self._ip: Optional[str] = None

    async def start(self) -> None:
        try:
            (MessageBus, BusType, Variant, ServiceInterface, dbus_property,
             method, PropertyAccess) = _import_dbus()
        except _ImportError as exc:
            logger.warning("Skipping extra BLE advertisement: %s", exc)
            _write_status("skipped")
            return

        service_uuid = self._service_uuid

        class _Advertisement(ServiceInterface):
            def __init__(self) -> None:
                super().__init__(ADV_IFACE)
                self._mfg: Optional[bytes] = None

            def set_manufacturer(self, payload: Optional[bytes]) -> None:
                self._mfg = payload
                self.emit_properties_changed({"ManufacturerData": self._wrap_mfg()})

            def _wrap_mfg(self):
                if self._mfg is None:
                    return {}
                return {INKYPI_COMPANY_ID: Variant("ay", list(self._mfg))}

            @dbus_property(access=PropertyAccess.READ)
            def Type(self) -> "s":  # noqa: N802, F821
                return "peripheral"

            @dbus_property(access=PropertyAccess.READ)
            def ServiceUUIDs(self) -> "as":  # noqa: N802, F821
                return [service_uuid]

            @dbus_property(access=PropertyAccess.READ)
            def ManufacturerData(self) -> "a{qv}":  # noqa: N802, F821
                return self._wrap_mfg()

            @dbus_property(access=PropertyAccess.READ)
            def MinInterval(self) -> "u":  # noqa: N802, F821
                return MIN_INTERVAL_SLOTS

            @dbus_property(access=PropertyAccess.READ)
            def MaxInterval(self) -> "u":  # noqa: N802, F821
                return MAX_INTERVAL_SLOTS

            @dbus_property(access=PropertyAccess.READ)
            def Discoverable(self) -> "b":  # noqa: N802, F821
                return True

            @method()
            def Release(self) -> None:  # noqa: N802
                logger.info("Extra BLE advertisement released by BlueZ")

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._adv = _Advertisement()
            # Apply any IP that was set before start() got called.
            payload = _pack_manufacturer_data(self._mode, self._ip)
            if payload is not None:
                self._adv.set_manufacturer(payload)
            self._bus.export(ADV_PATH, self._adv)

            introspection = await self._bus.introspect(BLUEZ_BUS, ADV_MGR_PATH)
            obj = self._bus.get_proxy_object(BLUEZ_BUS, ADV_MGR_PATH, introspection)
            mgr = obj.get_interface(ADV_MGR_IFACE)
            await mgr.call_register_advertisement(ADV_PATH, {})
            self._registered = True
            logger.info(
                "Extra BLE advertisement registered (interval %dms-%dms, mfg=%s)",
                int(MIN_INTERVAL_SLOTS * 0.625),
                int(MAX_INTERVAL_SLOTS * 0.625),
                payload.hex() if payload else "<none>",
            )
            _write_status("registered", payload.hex() if payload else None)
        except Exception:
            logger.exception("Failed to register extra BLE advertisement")
            _write_status("skipped")
            await self.stop()

    def update_address(self, mode: Optional[str], ip: Optional[str]) -> None:
        """Re-pack manufacturer data when Wi-Fi state changes. Safe to
        call before start() — the new value is applied when the advert
        is exported.
        """
        if mode == self._mode and ip == self._ip:
            return
        self._mode, self._ip = mode, ip
        if self._adv is not None:
            payload = _pack_manufacturer_data(mode, ip)
            self._adv.set_manufacturer(payload)
            logger.debug(
                "Extra advertisement mfg updated: %s",
                payload.hex() if payload else "<none>",
            )
            if self._registered:
                _write_status("registered", payload.hex() if payload else None)

    async def stop(self) -> None:
        if self._bus is None:
            return
        try:
            if self._registered:
                introspection = await self._bus.introspect(BLUEZ_BUS, ADV_MGR_PATH)
                obj = self._bus.get_proxy_object(BLUEZ_BUS, ADV_MGR_PATH, introspection)
                mgr = obj.get_interface(ADV_MGR_IFACE)
                await mgr.call_unregister_advertisement(ADV_PATH)
        except Exception:
            logger.warning("UnregisterAdvertisement failed during shutdown", exc_info=True)
        try:
            self._bus.disconnect()
        except Exception:
            pass
        self._bus = None
        self._adv = None
        self._registered = False
        _write_status("skipped")
