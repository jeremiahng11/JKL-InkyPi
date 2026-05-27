"""BlueZ pairing agent — accepts incoming BLE pair requests silently.

Without an agent, BlueZ rejects every pairing attempt the Android side
makes ("no agent available"), and Android keeps re-prompting the user
on each reconnect. With a registered ``NoInputNoOutput`` agent, BlueZ
completes the bond via JustWorks (no PIN, no confirmation UI on either
side), Android stores the bond, and the prompt loop ends.

The agent is registered against ``org.bluez`` over the system bus using
``dbus-fast`` — already a transitive dependency of ``bless``, so no
extra packages required.

Lifecycle:
- :func:`start` exports the agent object, calls ``RegisterAgent``
  and then ``RequestDefaultAgent`` so BlueZ routes pair callbacks to us.
- :func:`stop` unregisters and tears down the bus connection. Safe to
  call multiple times.

The methods on :class:`_AgentInterface` are the BlueZ ``Agent1``
contract; for JustWorks we just need to *not raise* — the default
return values (``None`` / empty string) plus a Pairable=true adapter
are enough to complete the bond.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("inkypi-ble.agent")

AGENT_PATH = "/org/inkypi/ble/agent"
BLUEZ_BUS = "org.bluez"
BLUEZ_MGR = "/org/bluez"
AGENT_MGR_IFACE = "org.bluez.AgentManager1"
AGENT_IFACE = "org.bluez.Agent1"


class _ImportError(RuntimeError):
    pass


def _import_dbus():
    try:
        from dbus_fast.aio import MessageBus  # type: ignore
        from dbus_fast.constants import BusType  # type: ignore
        from dbus_fast.service import ServiceInterface, method  # type: ignore
    except ImportError as exc:
        raise _ImportError(
            f"dbus-fast unavailable ({exc}); pairing agent will be skipped"
        ) from exc
    return MessageBus, BusType, ServiceInterface, method


class PairingAgent:
    """Persistent NoInputNoOutput agent that silently accepts pair requests."""

    def __init__(self) -> None:
        self._bus = None
        self._registered = False

    async def start(self) -> None:
        try:
            MessageBus, BusType, ServiceInterface, method = _import_dbus()
        except _ImportError as exc:
            logger.warning("%s", exc)
            return

        class _AgentInterface(ServiceInterface):
            def __init__(self) -> None:
                super().__init__(AGENT_IFACE)

            # All BlueZ Agent1 callbacks below are best-effort silent
            # acceptance. We don't raise; we don't prompt; we don't
            # confirm — exactly what JustWorks bonding needs.
            @method()
            def Release(self):  # noqa: N802 — BlueZ-defined name
                logger.info("Pairing agent released by BlueZ")

            @method()
            def AuthorizeService(self, device: "o", uuid: "s"):  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("AuthorizeService(%s, %s) → accept", device, uuid)

            @method()
            def RequestPinCode(self, device: "o") -> "s":  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("RequestPinCode(%s) → 0000", device)
                return "0000"

            @method()
            def RequestPasskey(self, device: "o") -> "u":  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("RequestPasskey(%s) → 0", device)
                return 0

            @method()
            def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("DisplayPasskey(%s, %s)", device, passkey)

            @method()
            def DisplayPinCode(self, device: "o", pincode: "s"):  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("DisplayPinCode(%s, %s)", device, pincode)

            @method()
            def RequestConfirmation(self, device: "o", passkey: "u"):  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("RequestConfirmation(%s, %s) → accept", device, passkey)

            @method()
            def RequestAuthorization(self, device: "o"):  # type: ignore[name-defined]  # noqa: N802, F821
                logger.debug("RequestAuthorization(%s) → accept", device)

            @method()
            def Cancel(self):  # noqa: N802
                logger.info("Pair request cancelled by remote")

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._bus.export(AGENT_PATH, _AgentInterface())

            introspection = await self._bus.introspect(BLUEZ_BUS, BLUEZ_MGR)
            obj = self._bus.get_proxy_object(BLUEZ_BUS, BLUEZ_MGR, introspection)
            mgr = obj.get_interface(AGENT_MGR_IFACE)
            # "NoInputNoOutput" → JustWorks pairing, no UI on either side.
            await mgr.call_register_agent(AGENT_PATH, "NoInputNoOutput")
            await mgr.call_request_default_agent(AGENT_PATH)
            self._registered = True
            logger.info("Registered NoInputNoOutput pairing agent at %s", AGENT_PATH)
        except Exception:
            logger.exception("Failed to register BlueZ pairing agent")
            await self.stop()

    async def stop(self) -> None:
        if self._bus is None:
            return
        try:
            if self._registered:
                introspection = await self._bus.introspect(BLUEZ_BUS, BLUEZ_MGR)
                obj = self._bus.get_proxy_object(BLUEZ_BUS, BLUEZ_MGR, introspection)
                mgr = obj.get_interface(AGENT_MGR_IFACE)
                await mgr.call_unregister_agent(AGENT_PATH)
        except Exception:
            logger.warning("UnregisterAgent failed during shutdown", exc_info=True)
        try:
            self._bus.disconnect()
        except Exception:
            pass
        self._bus = None
        self._registered = False
