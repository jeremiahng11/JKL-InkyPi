"""GATT peripheral entrypoint — wires the handlers up to ``bless`` and runs
the asyncio loop.

Designed to be invoked as ``python -m ble.service`` from
``inkypi-ble.service``. The module-level :func:`main` is the systemd entry
point; individual handler classes (``CommandHandler`` etc.) are also
unit-testable without standing up a real GATT server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.config
import os
import signal
import socket
import sys
from typing import Optional

from ble import framing
from ble.bridge import FlaskBridge
from ble.extra_adv import ExtraAdvertisement
from ble.gatt import ASSUMED_MTU, CHAR_UUIDS, SERVICE_UUID
from ble.handlers import CommandHandler, UploadHandler, WifiHandler
from ble.info import build_info
from ble.pairing_agent import PairingAgent
from network import hotspot, wifi as wifi_status

logger = logging.getLogger("inkypi-ble")

CONFIG_FILE = os.environ.get(
    "INKYPI_CONFIG_FILE",
    os.path.join(os.path.dirname(__file__), "..", "config", "device.json"),
)
LOGGING_CONF = os.path.join(os.path.dirname(__file__), "..", "config", "logging.conf")

INFO_REFRESH_INTERVAL = 15.0       # seconds
ADV_WATCHDOG_INTERVAL = 30.0       # seconds — how often to verify BlueZ
                                   # still has our advertisement registered.
                                   # bless 0.3.0 silently loses advertising
                                   # state on some central disconnects.


class _BlessUnavailable(RuntimeError):
    """Raised when the ``bless`` library can't be imported (e.g. dev machine
    without BlueZ)."""


def _import_bless():
    try:
        from bless import (  # type: ignore
            BlessServer,
            GATTAttributePermissions,
            GATTCharacteristicProperties,
        )
    except ImportError as exc:
        # Surface the underlying error rather than asserting bless is missing —
        # an import failure on Linux is usually a transitive dep / Python
        # version compatibility issue (e.g. dbus-fast on Python 3.13), not a
        # missing package.
        raise _BlessUnavailable(
            f"Could not import bless ({exc.__class__.__name__}: {exc}). "
            "If bless appears installed, this is most likely a transitive "
            "dependency or Python version compatibility issue."
        ) from exc
    return BlessServer, GATTCharacteristicProperties, GATTAttributePermissions


class BleService:
    def __init__(self) -> None:
        self.bridge = FlaskBridge()
        self.hotspot_cfg = self._load_hotspot_config()
        self.command_handler = CommandHandler(self.bridge, config_file=CONFIG_FILE)
        self.wifi_handler = WifiHandler(lambda: self.hotspot_cfg)
        self.upload_handler = UploadHandler(self.bridge)
        self.server = None
        self.mtu = ASSUMED_MTU
        self._stop_event: Optional[asyncio.Event] = None
        self.pairing_agent = PairingAgent()
        self.extra_adv = ExtraAdvertisement(SERVICE_UUID)

    # ------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        BlessServer, GATTCharacteristicProperties, GATTAttributePermissions = _import_bless()

        device_name = self._device_name()
        self.server = BlessServer(name=device_name)
        self.server.read_request_func = self._on_read
        self.server.write_request_func = self._on_write

        await self.server.add_new_service(SERVICE_UUID)

        # Characteristic definitions — keep in lockstep with docs/bluetooth.md
        await self._add_char(
            CHAR_UUIDS["info"],
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            GATTAttributePermissions.readable,
            initial_value=build_info(CONFIG_FILE, self.hotspot_cfg, self.bridge),
        )
        await self._add_char(
            CHAR_UUIDS["cmd"],
            GATTCharacteristicProperties.write,
            GATTAttributePermissions.writeable,
        )
        await self._add_char(
            CHAR_UUIDS["resp"],
            GATTCharacteristicProperties.notify,
            GATTAttributePermissions.readable,
        )
        await self._add_char(
            CHAR_UUIDS["wifi"],
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.notify,
            GATTAttributePermissions.writeable | GATTAttributePermissions.readable,
        )
        await self._add_char(
            CHAR_UUIDS["upc"],
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.notify,
            GATTAttributePermissions.writeable | GATTAttributePermissions.readable,
        )
        await self._add_char(
            CHAR_UUIDS["upd"],
            GATTCharacteristicProperties.write_without_response,
            GATTAttributePermissions.writeable,
        )

        await self.server.start()
        logger.info("BLE peripheral '%s' advertising service %s", device_name, SERVICE_UUID)

        # Register a JustWorks pairing agent so incoming bond requests
        # from Android complete silently instead of triggering an endless
        # "Pair?" dialog loop. Failure is non-fatal — the BLE service
        # still works without bonding, the user just sees the prompt.
        try:
            await self.pairing_agent.start()
        except Exception:
            logger.exception("Pairing agent failed to start — pair prompts may recur")

        # Start the secondary advertisement that carries Wi-Fi IP +
        # uses a faster advertising interval. Non-fatal on failure —
        # bless's own advertisement keeps running, just without the
        # extra speed + IP-in-adv benefits.
        try:
            self._refresh_extra_adv()
            await self.extra_adv.start()
        except Exception:
            logger.exception("Extra advertisement failed to start")

        self._stop_event = asyncio.Event()
        info_task = asyncio.create_task(self._info_refresher())
        watchdog_task = asyncio.create_task(self._advertisement_watchdog())
        try:
            await self._stop_event.wait()
        finally:
            for task in (info_task, watchdog_task):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # bless's server.stop() calls UnregisterAdvertisement which
            # raises DBusError("Does Not Exist") if BlueZ has already
            # dropped the advertisement (e.g. after an Android disconnect).
            # Swallow it — we're shutting down anyway.
            try:
                await self.server.stop()
            except Exception as exc:
                logger.warning("server.stop() raised %s — likely already torn down", exc)
            try:
                await self.pairing_agent.stop()
            except Exception:
                logger.warning("pairing_agent.stop() raised", exc_info=True)
            try:
                await self.extra_adv.stop()
            except Exception:
                logger.warning("extra_adv.stop() raised", exc_info=True)
            self.bridge.close()
            logger.info("BLE peripheral stopped")

    def request_stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    # -------------------------------------------------------------- GATT glue

    async def _add_char(self, uuid, properties, permissions, initial_value: bytes = b""):
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            uuid,
            properties,
            initial_value,
            permissions,
        )

    def _on_read(self, characteristic, **_) -> bytes:
        uuid = str(characteristic.uuid).lower()
        if uuid == CHAR_UUIDS["info"]:
            return build_info(CONFIG_FILE, self.hotspot_cfg, self.bridge)
        return characteristic.value or b""

    def _on_write(self, characteristic, value: bytes, **_) -> None:
        uuid = str(characteristic.uuid).lower()
        try:
            if uuid == CHAR_UUIDS["cmd"]:
                response = self.command_handler.handle(value)
                self._notify_fragmented(CHAR_UUIDS["resp"], response)
            elif uuid == CHAR_UUIDS["wifi"]:
                response = self.wifi_handler.handle(value)
                self._notify_fragmented(CHAR_UUIDS["wifi"], response)
                # Wi-Fi state may have changed — push an INFO update.
                self._push_info()
            elif uuid == CHAR_UUIDS["upc"]:
                response = self.upload_handler.handle_control(value)
                self._notify_fragmented(CHAR_UUIDS["upc"], response)
            elif uuid == CHAR_UUIDS["upd"]:
                self.upload_handler.handle_data(value)
            else:
                logger.warning("Write to unhandled characteristic %s", uuid)
        except Exception as exc:
            logger.exception("Write handler crashed on %s", uuid)
            err = json.dumps({"status": "error", "error": str(exc)}).encode("utf-8")
            self._notify_fragmented(CHAR_UUIDS["resp"], err, error=True)

    # ----------------------------------------------------------- notifications

    def _notify_fragmented(self, char_uuid: str, payload: bytes, *, error: bool = False) -> None:
        """Send ``payload`` over the given characteristic, fragmenting as
        needed so each notification fits inside the negotiated MTU."""
        if not self.server:
            return
        char = self.server.get_characteristic(char_uuid)
        if char is None:
            logger.error("Cannot notify; characteristic %s not found", char_uuid)
            return
        for frame in framing.fragment_payload(payload, mtu=self.mtu):
            if error:
                # Force the LAST | ERROR combo on the final frame regardless of
                # what fragment_payload picked.
                frame = bytes([framing.FRAG_LAST | framing.FRAG_ERROR]) + frame[1:]
            char.value = frame
            self.server.update_value(SERVICE_UUID, char_uuid)

    def _push_info(self) -> None:
        if not self.server:
            return
        char = self.server.get_characteristic(CHAR_UUIDS["info"])
        if char is None:
            return
        char.value = build_info(CONFIG_FILE, self.hotspot_cfg, self.bridge)
        self.server.update_value(SERVICE_UUID, CHAR_UUIDS["info"])
        # Keep the BLE-adv IP in lockstep with INFO so scanners that
        # never connect still see the current Wi-Fi address.
        self._refresh_extra_adv()

    def _refresh_extra_adv(self) -> None:
        """Pack the current wifi mode + IP into the extra advert. No-op
        when wifi isn't reachable or the extra advert hasn't been
        started yet."""
        try:
            status = wifi_status.current_status()
        except Exception:
            status = None
        if status is None:
            self.extra_adv.update_address(None, None)
            return
        self.extra_adv.update_address(status.mode, status.ip)

    async def _info_refresher(self) -> None:
        while True:
            try:
                await asyncio.sleep(INFO_REFRESH_INTERVAL)
                self._push_info()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("INFO refresh failed")

    async def _advertisement_watchdog(self) -> None:
        """Detect when BlueZ has dropped our advertisement and trigger a
        self-restart so systemd brings us back cleanly.

        bless 0.3.0 / BlueZ silently unregister the advertisement on some
        central disconnects (force-stop, out of range, screen off). The
        Python process keeps running but the radio is silent — neither
        nRF Connect nor our app can find the Pi until something restarts
        the service.
        """
        while True:
            try:
                await asyncio.sleep(ADV_WATCHDOG_INTERVAL)
                if not await self._advertising_active():
                    logger.warning(
                        "Advertisement no longer active — requesting stop so "
                        "systemd restarts the service"
                    )
                    self.request_stop()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Advertisement watchdog tick failed")

    @staticmethod
    async def _advertising_active() -> bool:
        """Returns True if BlueZ reports at least one active LE
        advertisement instance on the controller. Uses bluetoothctl
        rather than dbus-fast directly to keep the dependency surface
        small — the call is ~50ms on a Pi Zero 2 W."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "show",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            # bluetoothctl missing — can't verify, assume OK to avoid loops
            return True
        for raw in stdout.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line.startswith("ActiveInstances:"):
                token = line.split(":", 1)[1].strip().split()[0]
                try:
                    return int(token, 0) > 0
                except ValueError:
                    return False
        # ActiveInstances missing from output — assume OK rather than
        # restart-looping when bluetoothctl format changes.
        return True

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _device_name() -> str:
        hostname = socket.gethostname()
        return hostname if hostname.startswith("InkyPi") else f"InkyPi-{hostname}"

    @staticmethod
    def _load_hotspot_config() -> hotspot.HotspotConfig:
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            raw = cfg.get("hotspot") or {}
            if raw.get("ssid") and raw.get("password"):
                return hotspot.HotspotConfig(
                    ssid=raw["ssid"],
                    password=raw["password"],
                    profile=raw.get("profile", "InkyPi-AP"),
                    gateway=raw.get("gateway", "192.168.4.1"),
                )
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read hotspot config from %s", CONFIG_FILE)
        # Fall back to an ephemeral config — the netd service will persist a
        # proper one on its first run.
        return hotspot.HotspotConfig.generate(socket.gethostname())


def main() -> int:
    if os.path.exists(LOGGING_CONF):
        logging.config.fileConfig(LOGGING_CONF, disable_existing_loggers=False)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    service = BleService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.request_stop)

    try:
        loop.run_until_complete(service.run())
    except _BlessUnavailable as exc:
        logger.error("%s", exc)
        if exc.__cause__:
            logger.error("Underlying import error chain:", exc_info=exc.__cause__)
        return 1
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
