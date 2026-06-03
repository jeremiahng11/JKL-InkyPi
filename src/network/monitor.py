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

    # How many consecutive "no client connection" ticks we need to see
    # before flipping to AP mode. At the default 10s interval, 6 ticks
    # = ~60s of sustained downtime. Anything shorter and a single
    # nmcli hiccup or DHCP renewal tears the client connection down
    # for nothing — the original cause of the "Wi-Fi dies after a few
    # hours" bug.
    failover_after_consecutive: int = 6

    # After this many consecutive "no client" ticks we start actively
    # trying to bring saved Wi-Fi profiles up ourselves rather than
    # trusting NM to autoconnect. NM sometimes doesn't auto-reconnect
    # after a move — it last associated to a specific BSSID, then the
    # Pi is relocated and the SSID is back in range but on a different
    # AP, and NM sits there until something nudges it. Defaults to 2
    # (~20s after the first failure), well before AP failover so the
    # user gets the home Wi-Fi back if it's reachable.
    rescue_saved_after_consecutive: int = 2

    # Optional WAN reachability check. When None (default) we trust the
    # NM client-mode + IP signal alone — flipping to AP just because a
    # 3s TCP probe to a public DNS resolver failed is a net loss: the
    # user is usually on the same LAN as the Pi and tearing down the
    # client connection drops them too. Set to a (host, port, timeout)
    # tuple if you really want to demand WAN connectivity; it then only
    # counts a tick as "failed" when BOTH NM says no-client AND the
    # probe fails (never the other way round).
    reachability_probe: Optional[tuple[str, int, float]] = None


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
        # Counts ticks where NM reported no usable client connection.
        # Resets to 0 the moment we see a healthy client again.
        self._consecutive_failures = 0
        # Profiles we've tried to bring up during the current outage.
        # Cleared whenever a healthy client returns so the next outage
        # starts fresh.
        self._attempted_profiles: set[str] = set()

    def run_forever(self) -> None:
        logger.info("Connectivity monitor started")
        try:
            while True:
                self.tick()
                self._sleep(self.options.interval_seconds)
        except KeyboardInterrupt:
            logger.info("Connectivity monitor interrupted")

    def tick(self) -> None:
        """Inspect Wi-Fi state once and update AP accordingly.

        Decision tree (precedence from top):

          1. NM reports we're a Wi-Fi client and we have an IP address →
             the link is up regardless of whether the public Internet is
             reachable. Stay on client mode; bringing the AP up here
             would only sever the LAN connection the user has to the Pi.
          2. Otherwise count the failure. Only fail over to AP after
             ``failover_after_consecutive`` *consecutive* tick failures
             so single-tick blips (nmcli temporary error, mid-flight
             DHCP renewal, antenna sleep) don't tear down a working
             connection.
        """
        try:
            status = wifi.current_status()
        except Exception:
            logger.exception("Failed to read Wi-Fi status")
            return

        if status.mode == "client" and status.ip:
            if self._consecutive_failures > 0:
                logger.info(
                    "Wi-Fi client connection back (was %d consecutive failures)",
                    self._consecutive_failures,
                )
            self._consecutive_failures = 0
            self._attempted_profiles.clear()
            self._ensure_ap_down()
            return

        # Boot grace: don't flip into AP mode while NM is still
        # negotiating the initial client connection. We DO let the
        # failure counter accumulate during this window so we recover
        # quickly the moment grace ends if there's still no client.
        within_boot_grace = (
            (self._clock() - self._started_at) < self.options.client_grace_seconds
        )

        # Optional WAN probe — disabled by default. When set, only ticks
        # where BOTH client is missing AND WAN is unreachable count as
        # failures. We never use WAN-unreachable alone to tear down a
        # working client connection; that's what bit the original
        # implementation.
        wan_required_failure = True
        if self.options.reachability_probe is not None:
            wan_required_failure = not self._reachable(self.options.reachability_probe)
        if wan_required_failure:
            self._consecutive_failures += 1

        # Step in BEFORE the boot-grace check so we can rescue the
        # saved profile even during boot — that's the exact window
        # where NM auto-connect commonly stalls after a relocation.
        if (
            self._consecutive_failures
            >= self.options.rescue_saved_after_consecutive
            and status.mode != "ap"
        ):
            self._try_rescue_saved_profile()

        if within_boot_grace and status.mode != "ap":
            logger.debug(
                "Within boot grace period; deferring AP fallback "
                "(%d/%d failures so far)",
                self._consecutive_failures,
                self.options.failover_after_consecutive,
            )
            return

        if self._consecutive_failures < self.options.failover_after_consecutive:
            logger.debug(
                "No client connection but only %d/%d consecutive failures; "
                "deferring AP fallback",
                self._consecutive_failures,
                self.options.failover_after_consecutive,
            )
            return

        if status.mode != "ap":
            logger.info(
                "No client connection for %d consecutive ticks "
                "(~%.0fs); bringing AP fallback up",
                self._consecutive_failures,
                self._consecutive_failures * self.options.interval_seconds,
            )
        self._ensure_ap_up()

    # ------------------------------------------------------------------ helpers

    def _try_rescue_saved_profile(self) -> None:
        """Walk the saved Wi-Fi profile list and explicitly `nmcli con up`
        each one we haven't tried yet during the current outage.

        Catches the common "Pi was moved, NM didn't autoconnect, no AP
        fallback because we're not at threshold yet" scenario the user
        reported: plug in at a new location, nothing comes up until
        someone SSHes in and manually nudges NM.

        Tries one profile per tick rather than blasting them all at
        once — keeps nmcli single-threaded and lets the next tick see
        whether the previous attempt actually succeeded before moving
        on.
        """
        try:
            saved = wifi.list_saved()
        except Exception:
            logger.exception("Could not enumerate saved Wi-Fi profiles")
            return
        candidates = [s for s in saved if s and s not in self._attempted_profiles]
        if not candidates:
            return
        target = candidates[0]
        self._attempted_profiles.add(target)
        logger.info(
            "No Wi-Fi client connection after %d ticks; trying to bring up "
            "saved profile '%s' explicitly",
            self._consecutive_failures, target,
        )
        try:
            ok = wifi.activate_saved(target, timeout=30)
        except Exception:
            logger.exception("Rescue activation of '%s' raised", target)
            ok = False
        if ok:
            logger.info("Saved profile '%s' activation requested", target)

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

    @staticmethod
    def _reachable(probe: tuple[str, int, float]) -> bool:
        """Optional WAN reachability check. Only used when
        ``MonitorOptions.reachability_probe`` is explicitly set."""
        host, port, timeout = probe
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False
