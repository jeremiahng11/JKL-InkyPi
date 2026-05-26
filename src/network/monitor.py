"""Connectivity monitor: keeps the Pi online by failing over to its own
access point when no upstream Wi-Fi is reachable.

Run as ``inkypi-netd.service``. Loop:
  1. Probe NetworkManager for the wlan0 status.
  2. If a client connection has an IPv4 address → make sure AP is down.
  3. Otherwise → bring AP up (so phone + BLE provisioning still works).
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from typing import Callable, Optional

from network import hotspot, wifi

logger = logging.getLogger(__name__)


@dataclass
class MonitorOptions:
    interval_seconds: float = 10.0
    client_grace_seconds: float = 45.0   # time to wait after boot before failing over
    reachability_host: str = "1.1.1.1"
    reachability_port: int = 53
    reachability_timeout: float = 3.0


class ConnectivityMonitor:
    """Polls Wi-Fi state and toggles the AP fallback profile.

    The class is deliberately tiny: any process that wants to embed it can
    instantiate, set ``hotspot_config``, and call :meth:`run_forever`. The
    monitor unit (``inkypi-netd``) is the typical owner.
    """

    def __init__(
        self,
        hotspot_config: hotspot.HotspotConfig,
        *,
        options: Optional[MonitorOptions] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.hotspot_config = hotspot_config
        self.options = options or MonitorOptions()
        self._clock = clock
        self._sleep = sleep
        self._started_at = clock()

    def run_forever(self) -> None:
        logger.info("Connectivity monitor started")
        try:
            while True:
                self.tick()
                self._sleep(self.options.interval_seconds)
        except KeyboardInterrupt:
            logger.info("Connectivity monitor interrupted")

    def tick(self) -> None:
        """Inspect Wi-Fi state once and update AP accordingly."""
        try:
            status = wifi.current_status()
        except Exception:
            logger.exception("Failed to read Wi-Fi status")
            return

        if status.mode == "client" and status.ip and self._reachable():
            self._ensure_ap_down()
            return

        # Grace period after boot — don't flip into AP mode if NM is still
        # negotiating a client connection.
        if (self._clock() - self._started_at) < self.options.client_grace_seconds:
            if status.mode != "ap":
                logger.debug("Within grace period; deferring AP fallback")
                return

        self._ensure_ap_up()

    # ------------------------------------------------------------------ helpers

    def _ensure_ap_up(self) -> None:
        if hotspot.is_active(self.hotspot_config.profile):
            return
        try:
            hotspot.start(self.hotspot_config)
        except Exception:
            logger.exception("Failed to start AP fallback")

    def _ensure_ap_down(self) -> None:
        if not hotspot.is_active(self.hotspot_config.profile):
            return
        try:
            hotspot.stop(self.hotspot_config)
        except Exception:
            logger.exception("Failed to stop AP fallback")

    def _reachable(self) -> bool:
        """Lightweight TCP probe to confirm the link actually carries traffic.

        Carrier + IP is not the same as Internet. We attempt a 3s TCP connect
        to a well-known DNS responder; failure means the AP fallback should
        stay on so the user can still talk to InkyPi locally.
        """
        try:
            with socket.create_connection(
                (self.options.reachability_host, self.options.reachability_port),
                timeout=self.options.reachability_timeout,
            ):
                return True
        except OSError:
            return False
