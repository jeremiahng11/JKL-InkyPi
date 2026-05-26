"""Handler for the CMD / RESP characteristics.

Receives JSON envelopes (see ``docs/bluetooth.md``) and dispatches each ``op``
to either a local read of ``device.json`` or an HTTP call against the Flask
app via :class:`ble.bridge.FlaskBridge`.

The handler is intentionally synchronous and pure-Python; the BLE service
schedules it from its asyncio loop.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict

from ble.bridge import BridgeResult, FlaskBridge

logger = logging.getLogger(__name__)


class CommandError(RuntimeError):
    """Raised by handlers to produce a structured error response."""


class CommandHandler:
    def __init__(
        self,
        bridge: FlaskBridge,
        *,
        config_file: str,
    ) -> None:
        self.bridge = bridge
        self.config_file = config_file
        self._ops: Dict[str, Callable[[dict], Any]] = {
            "list_plugins":            self._op_list_plugins,
            "list_playlists":          self._op_list_playlists,
            "get_settings":            self._op_get_settings,
            "save_settings":           self._op_save_settings,
            "display_plugin_instance": self._op_display_plugin_instance,
            "create_playlist":         self._op_create_playlist,
            "update_playlist":         self._op_update_playlist,
            "delete_playlist":         self._op_delete_playlist,
            "delete_plugin_instance":  self._op_delete_plugin_instance,
            "set_plugin_order":        self._op_set_plugin_order,
            "set_apikey":              self._op_set_apikey,
            "refresh_now":             self._op_refresh_now,
            "reboot":                  self._op_reboot,
            "shutdown":                self._op_shutdown,
        }

    # ------------------------------------------------------------ public entry

    def handle(self, raw: bytes) -> bytes:
        """Process a single CMD write and return the JSON RESP payload."""
        request_id = ""
        try:
            envelope = json.loads(raw.decode("utf-8"))
            request_id = str(envelope.get("id", ""))
            op = envelope.get("op")
            data = envelope.get("data") or {}
            if not isinstance(data, dict):
                raise CommandError("'data' must be an object")
            handler = self._ops.get(op)
            if not handler:
                raise CommandError(f"unknown op: {op}")
            result = handler(data)
            response = {"id": request_id, "status": "ok", "data": result}
        except CommandError as exc:
            response = {"id": request_id, "status": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception("Command handler crash on raw=%r", raw[:128])
            response = {"id": request_id, "status": "error", "error": f"internal: {exc}"}
        return json.dumps(response, separators=(",", ":")).encode("utf-8")

    # ----------------------------------------------------------------- helpers

    def _read_config(self) -> dict:
        if not os.path.exists(self.config_file):
            raise CommandError("device.json not found")
        with open(self.config_file) as f:
            return json.load(f)

    @staticmethod
    def _bridge_result(result: BridgeResult) -> Any:
        if result.ok:
            return result.body
        if isinstance(result.body, dict) and "error" in result.body:
            raise CommandError(result.body["error"])
        raise CommandError(f"bridge error: HTTP {result.status_code}")

    # --------------------------------------------------------- read operations

    def _op_list_plugins(self, _data: dict) -> Any:
        cfg = self._read_config()
        # Plugin definitions live on disk, but the per-instance settings are
        # in the playlist. Return both so the app can render a single screen.
        order = cfg.get("plugin_order") or []
        return {
            "plugin_order": order,
            "plugins": cfg.get("plugin_order_metadata", []),
        }

    def _op_list_playlists(self, _data: dict) -> Any:
        cfg = self._read_config()
        return cfg.get("playlist_config", {})

    def _op_get_settings(self, _data: dict) -> Any:
        cfg = self._read_config()
        # Strip large / sensitive blocks; the app only needs the user-tunable
        # settings panel.
        return {
            "name":                            cfg.get("name"),
            "orientation":                     cfg.get("orientation"),
            "inverted_image":                  cfg.get("inverted_image"),
            "log_system_stats":                cfg.get("log_system_stats"),
            "timezone":                        cfg.get("timezone"),
            "time_format":                     cfg.get("time_format"),
            "plugin_cycle_interval_seconds":   cfg.get("plugin_cycle_interval_seconds"),
            "image_settings":                  cfg.get("image_settings", {}),
            "resolution":                      cfg.get("resolution"),
            "display_type":                    cfg.get("display_type"),
        }

    # -------------------------------------------------------- write operations

    def _op_save_settings(self, data: dict) -> Any:
        # /save_settings is form-encoded with specific field names. Translate
        # the JSON keys the app sends into that schema.
        form = {
            "deviceName":     data.get("name", ""),
            "orientation":    data.get("orientation", "horizontal"),
            "invertImage":    str(data.get("inverted_image", "")),
            "logSystemStats": str(data.get("log_system_stats", "")),
            "timezoneName":   data.get("timezone", ""),
            "timeFormat":     data.get("time_format", "24h"),
            "unit":           data.get("cycle_unit", "minute"),
            "interval":       str(data.get("cycle_interval", "5")),
            "saturation":     str(data.get("saturation", "1.0")),
            "brightness":     str(data.get("brightness", "1.0")),
            "sharpness":      str(data.get("sharpness", "1.0")),
            "contrast":       str(data.get("contrast", "1.0")),
        }
        if "inky_saturation" in data:
            form["inky_saturation"] = str(data["inky_saturation"])
        return self._bridge_result(self.bridge.post_form("/save_settings", data=form))

    def _op_display_plugin_instance(self, data: dict) -> Any:
        return self._bridge_result(
            self.bridge.post_json("/display_plugin_instance", payload=data)
        )

    def _op_create_playlist(self, data: dict) -> Any:
        return self._bridge_result(self.bridge.post_json("/create_playlist", payload=data))

    def _op_update_playlist(self, data: dict) -> Any:
        name = data.pop("playlist_name", None)
        if not name:
            raise CommandError("playlist_name is required")
        return self._bridge_result(self.bridge.put_json(f"/update_playlist/{name}", payload=data))

    def _op_delete_playlist(self, data: dict) -> Any:
        name = data.get("playlist_name")
        if not name:
            raise CommandError("playlist_name is required")
        return self._bridge_result(self.bridge.delete(f"/delete_playlist/{name}"))

    def _op_delete_plugin_instance(self, data: dict) -> Any:
        return self._bridge_result(
            self.bridge.post_json("/delete_plugin_instance", payload=data)
        )

    def _op_set_plugin_order(self, data: dict) -> Any:
        order = data.get("order")
        if not isinstance(order, list):
            raise CommandError("order must be a list")
        return self._bridge_result(self.bridge.post_json("/api/plugin_order", payload={"order": order}))

    def _op_set_apikey(self, data: dict) -> Any:
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise CommandError("entries must be a list")
        return self._bridge_result(self.bridge.post_json("/api-keys/save", payload={"entries": entries}))

    def _op_refresh_now(self, _data: dict) -> Any:
        # /update_now requires a plugin_id and the existing settings. For
        # "refresh whatever is currently on screen" the Flask app uses the
        # refresh task's cycle; no dedicated endpoint exists. Best-effort:
        # re-display the most recently displayed playlist instance.
        cfg = self._read_config()
        last = cfg.get("refresh_info", {}).get("plugin_instance")
        playlist = cfg.get("refresh_info", {}).get("playlist")
        plugin_id = cfg.get("refresh_info", {}).get("plugin_id")
        if not (last and playlist and plugin_id):
            raise CommandError("no previous display info available to refresh")
        return self._bridge_result(self.bridge.post_json(
            "/display_plugin_instance",
            payload={"playlist_name": playlist, "plugin_id": plugin_id, "plugin_instance": last},
        ))

    def _op_reboot(self, _data: dict) -> Any:
        return self._bridge_result(self.bridge.post_json("/shutdown", payload={"reboot": True}))

    def _op_shutdown(self, _data: dict) -> Any:
        return self._bridge_result(self.bridge.post_json("/shutdown", payload={}))
