"""Handler for the WIFI characteristic.

Wraps the :mod:`network` helpers so the phone can scan, join, and forget
Wi-Fi networks even when Flask is not (yet) reachable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any, Callable, Dict

from network import hotspot, wifi
from network._nm import NmcliError

logger = logging.getLogger(__name__)


class WifiCommandError(RuntimeError):
    pass


class WifiHandler:
    def __init__(self, get_hotspot_config: Callable[[], hotspot.HotspotConfig]) -> None:
        self._get_hotspot_config = get_hotspot_config
        self._ops: Dict[str, Callable[[dict], Any]] = {
            "scan":       self._op_scan,
            "connect":    self._op_connect,
            "forget":     self._op_forget,
            "status":     self._op_status,
            "ap_enable":  self._op_ap_enable,
            "ap_disable": self._op_ap_disable,
        }

    def handle(self, raw: bytes) -> bytes:
        request_id = ""
        try:
            envelope = json.loads(raw.decode("utf-8"))
            request_id = str(envelope.get("id", ""))
            op = envelope.get("op")
            data = envelope.get("data") or {}
            handler = self._ops.get(op)
            if not handler:
                raise WifiCommandError(f"unknown wifi op: {op}")
            result = handler(data)
            response = {"id": request_id, "status": "ok", "data": result}
        except (WifiCommandError, NmcliError, ValueError) as exc:
            response = {"id": request_id, "status": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception("Wifi handler crash on raw=%r", raw[:128])
            response = {"id": request_id, "status": "error", "error": f"internal: {exc}"}
        return json.dumps(response, separators=(",", ":")).encode("utf-8")

    # ------------------------------------------------------------------------

    def _op_scan(self, _data: dict) -> Any:
        return {"networks": [asdict(n) for n in wifi.scan()]}

    def _op_connect(self, data: dict) -> Any:
        ssid = data.get("ssid")
        if not ssid:
            raise WifiCommandError("ssid is required")
        password = data.get("password") or None
        status = wifi.connect(ssid, password)
        return status.to_dict()

    def _op_forget(self, data: dict) -> Any:
        ssid = data.get("ssid")
        if not ssid:
            raise WifiCommandError("ssid is required")
        wifi.forget(ssid)
        return {"forgotten": ssid}

    def _op_status(self, _data: dict) -> Any:
        return wifi.current_status().to_dict()

    def _op_ap_enable(self, _data: dict) -> Any:
        cfg = self._get_hotspot_config()
        hotspot.start(cfg)
        return {"ssid": cfg.ssid, "password": cfg.password, "gateway": cfg.gateway}

    def _op_ap_disable(self, _data: dict) -> Any:
        cfg = self._get_hotspot_config()
        hotspot.stop(cfg)
        return {"profile": cfg.profile, "active": hotspot.is_active(cfg.profile)}
