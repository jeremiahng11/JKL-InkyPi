"""Unit tests for :class:`ble.handlers.commands.CommandHandler`.

The handler is exercised with a fake :class:`FlaskBridge` so we can assert
that each BLE op maps to the right HTTP call without spinning up Flask.
"""

import json
from typing import Any, Dict, List, Tuple

import pytest

from ble.bridge import BridgeResult
from ble.handlers.commands import CommandHandler


class FakeBridge:
    """Records calls and replays canned responses."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []
        self.next_result = BridgeResult(status_code=200, body={"success": True})

    # The handler only uses these methods; matching the real bridge API.
    def post_form(self, path, data=None, files=None):
        self.calls.append(("POST_FORM", path, {"data": data, "files": files}))
        return self.next_result

    def post_json(self, path, payload):
        self.calls.append(("POST_JSON", path, payload))
        return self.next_result

    def put_json(self, path, payload):
        self.calls.append(("PUT_JSON", path, payload))
        return self.next_result

    def delete(self, path):
        self.calls.append(("DELETE", path, {}))
        return self.next_result


@pytest.fixture
def fake_config(tmp_path):
    config_file = tmp_path / "device.json"
    config_file.write_text(json.dumps({
        "name": "TestPi",
        "orientation": "horizontal",
        "timezone": "Asia/Singapore",
        "time_format": "24h",
        "resolution": [800, 480],
        "plugin_cycle_interval_seconds": 300,
        "plugin_order": ["clock", "weather"],
        "plugin_order_metadata": [
            {"id": "clock", "display_name": "Clock"},
            {"id": "weather", "display_name": "Weather"},
        ],
        "playlist_config": {"playlists": []},
        "refresh_info": {
            "plugin_id": "weather",
            "playlist": "Default",
            "plugin_instance": "Home Weather",
        },
    }))
    return str(config_file)


@pytest.fixture
def handler(fake_config):
    return CommandHandler(FakeBridge(), config_file=fake_config), None


def _invoke(handler: CommandHandler, op: str, data=None, request_id="r1") -> dict:
    envelope = {"id": request_id, "op": op}
    if data is not None:
        envelope["data"] = data
    raw_response = handler.handle(json.dumps(envelope).encode())
    return json.loads(raw_response.decode())


class TestReadOps:
    def test_get_settings_returns_subset(self, fake_config):
        h = CommandHandler(FakeBridge(), config_file=fake_config)
        resp = _invoke(h, "get_settings")
        assert resp["status"] == "ok"
        assert resp["id"] == "r1"
        assert resp["data"]["name"] == "TestPi"
        assert resp["data"]["resolution"] == [800, 480]

    def test_list_plugins_returns_order_and_metadata(self, fake_config):
        h = CommandHandler(FakeBridge(), config_file=fake_config)
        resp = _invoke(h, "list_plugins")
        assert resp["status"] == "ok"
        assert resp["data"]["plugin_order"] == ["clock", "weather"]
        assert len(resp["data"]["plugins"]) == 2

    def test_list_playlists_returns_playlist_block(self, fake_config):
        h = CommandHandler(FakeBridge(), config_file=fake_config)
        resp = _invoke(h, "list_playlists")
        assert resp["status"] == "ok"
        assert "playlists" in resp["data"]


class TestWriteOpsBridgeCalls:
    def test_save_settings_translates_to_form_post(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        resp = _invoke(h, "save_settings", data={
            "name": "Newname", "orientation": "vertical", "timezone": "UTC",
            "time_format": "12h", "cycle_unit": "hour", "cycle_interval": "1",
        })
        assert resp["status"] == "ok"
        assert len(bridge.calls) == 1
        method, path, kwargs = bridge.calls[0]
        assert (method, path) == ("POST_FORM", "/save_settings")
        assert kwargs["data"]["deviceName"] == "Newname"
        assert kwargs["data"]["timeFormat"] == "12h"
        assert kwargs["data"]["interval"] == "1"

    def test_display_plugin_instance_proxies_json(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        payload = {"playlist_name": "Default", "plugin_id": "clock", "plugin_instance": "Wall Clock"}
        _invoke(h, "display_plugin_instance", data=payload)
        assert bridge.calls == [("POST_JSON", "/display_plugin_instance", payload)]

    def test_update_playlist_pops_name_into_path(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        _invoke(h, "update_playlist", data={
            "playlist_name": "Default", "new_name": "Morning",
            "start_time": "06:00", "end_time": "12:00",
        })
        assert len(bridge.calls) == 1
        method, path, payload = bridge.calls[0]
        assert (method, path) == ("PUT_JSON", "/update_playlist/Default")
        assert payload == {"new_name": "Morning", "start_time": "06:00", "end_time": "12:00"}

    def test_delete_playlist_uses_path_name(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        _invoke(h, "delete_playlist", data={"playlist_name": "Morning"})
        assert bridge.calls == [("DELETE", "/delete_playlist/Morning", {})]

    def test_set_plugin_order_validates_list(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        resp = _invoke(h, "set_plugin_order", data={"order": "not a list"})
        assert resp["status"] == "error"
        assert "list" in resp["error"]
        assert bridge.calls == []

    def test_reboot_posts_reboot_flag(self, fake_config):
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=fake_config)
        _invoke(h, "reboot")
        assert bridge.calls == [("POST_JSON", "/shutdown", {"reboot": True})]


class TestRedisplayLast:
    """``redisplay_last`` re-shows the most recent playlist instance. It
    cannot replay a one-off ``ManualRefresh`` because those don't persist
    their settings, so the op must error clearly in that case."""

    def _config_with(self, tmp_path, refresh_info: dict | None) -> str:
        cfg = {
            "name": "TestPi",
            "playlist_config": {"playlists": []},
            "plugin_order": [],
            "plugin_order_metadata": [],
        }
        if refresh_info is not None:
            cfg["refresh_info"] = refresh_info
        path = tmp_path / "device.json"
        path.write_text(json.dumps(cfg))
        return str(path)

    def test_redisplays_previous_playlist_entry(self, tmp_path):
        path = self._config_with(tmp_path, {
            "refresh_type": "Playlist",
            "playlist": "Morning",
            "plugin_id": "weather",
            "plugin_instance": "Home Weather",
        })
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=path)
        resp = _invoke(h, "redisplay_last")
        assert resp["status"] == "ok"
        assert bridge.calls == [
            ("POST_JSON", "/display_plugin_instance", {
                "playlist_name": "Morning",
                "plugin_id": "weather",
                "plugin_instance": "Home Weather",
            }),
        ]

    def test_rejects_when_previous_was_manual_update(self, tmp_path):
        path = self._config_with(tmp_path, {
            "refresh_type": "Manual Update",
            "plugin_id": "image_upload",
        })
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=path)
        resp = _invoke(h, "redisplay_last")
        assert resp["status"] == "error"
        assert "one-off" in resp["error"]
        assert bridge.calls == []

    def test_rejects_when_refresh_info_empty(self, tmp_path):
        path = self._config_with(tmp_path, None)
        bridge = FakeBridge()
        h = CommandHandler(bridge, config_file=path)
        resp = _invoke(h, "redisplay_last")
        assert resp["status"] == "error"
        assert "never displayed" in resp["error"]
        assert bridge.calls == []

    def test_rejects_when_plugin_id_missing(self, tmp_path):
        # Defensive: playlist + instance present but plugin_id somehow not.
        # Shouldn't happen in practice but a malformed config shouldn't crash.
        path = self._config_with(tmp_path, {
            "refresh_type": "Playlist",
            "playlist": "Morning",
            "plugin_instance": "Home Weather",
        })
        h = CommandHandler(FakeBridge(), config_file=path)
        resp = _invoke(h, "redisplay_last")
        assert resp["status"] == "error"
        assert "plugin_id" in resp["error"]

    def test_old_op_name_refresh_now_is_not_dispatched(self, tmp_path):
        # During the rename we kept no backward-compat alias because the op
        # was never released. Make sure the old name is rejected, not
        # silently dispatched.
        path = self._config_with(tmp_path, {
            "refresh_type": "Playlist",
            "playlist": "Morning",
            "plugin_id": "weather",
            "plugin_instance": "Home Weather",
        })
        h = CommandHandler(FakeBridge(), config_file=path)
        resp = _invoke(h, "refresh_now")
        assert resp["status"] == "error"
        assert "unknown op" in resp["error"]


class TestErrorPaths:
    def test_unknown_op_returns_error(self, fake_config):
        h = CommandHandler(FakeBridge(), config_file=fake_config)
        resp = _invoke(h, "nonexistent")
        assert resp["status"] == "error"
        assert "unknown op" in resp["error"]

    def test_invalid_json_returns_error_with_blank_id(self, fake_config):
        h = CommandHandler(FakeBridge(), config_file=fake_config)
        resp_bytes = h.handle(b"not valid json")
        resp = json.loads(resp_bytes)
        assert resp["status"] == "error"
        assert resp["id"] == ""

    def test_bridge_error_surfaces_message(self, fake_config):
        bridge = FakeBridge()
        bridge.next_result = BridgeResult(status_code=400, body={"error": "missing field"})
        h = CommandHandler(bridge, config_file=fake_config)
        resp = _invoke(h, "create_playlist", data={"playlist_name": "X"})
        assert resp["status"] == "error"
        assert resp["error"] == "missing field"
