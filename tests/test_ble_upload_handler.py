"""End-to-end tests for the chunked BLE upload handler.

Simulates the full UPC + UPD sequence (``start`` -> N data chunks -> ``end``)
and asserts that the assembled file is correctly forwarded to the Flask
bridge. Uses a fake bridge that records calls.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

from ble.bridge import BridgeResult
from ble.handlers.upload import OP_DATA, MAX_UPLOAD_BYTES, UploadError, UploadHandler


class FakeBridge:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.next_result = BridgeResult(status_code=200, body={"success": True})

    def post_form(self, path, data=None, files=None):
        self.calls.append((path, {"data": data, "files": {k: (v[0], v[1][:32], v[2]) for k, v in (files or {}).items()}}))
        return self.next_result


def _start_envelope(name: str, size: int, sha256: str, **extra) -> bytes:
    data = {"name": name, "size": size, "sha256": sha256, **extra}
    return json.dumps({"id": "u1", "op": "start", "data": data}).encode()


def _end_envelope(upload_id: str) -> bytes:
    return json.dumps({"id": "u2", "op": "end", "data": {"upload_id": upload_id}}).encode()


def _data_frame(seq: int, body: bytes) -> bytes:
    return bytes([OP_DATA]) + seq.to_bytes(4, "big") + body


def _chunk(payload: bytes, chunk_size: int):
    for i in range(0, len(payload), chunk_size):
        yield payload[i : i + chunk_size]


@pytest.fixture
def handler() -> UploadHandler:
    return UploadHandler(FakeBridge(), mtu=247)


class TestStartValidation:
    def test_rejects_missing_name(self, handler):
        resp = json.loads(handler.handle_control(_start_envelope("", 100, "0" * 64)))
        assert resp["status"] == "error"

    def test_rejects_bad_size(self, handler):
        resp = json.loads(handler.handle_control(_start_envelope("x.png", -1, "0" * 64)))
        assert resp["status"] == "error"

    def test_rejects_oversize(self, handler):
        resp = json.loads(handler.handle_control(
            _start_envelope("x.png", MAX_UPLOAD_BYTES + 1, "0" * 64)
        ))
        assert resp["status"] == "error"
        assert "exceeds maximum" in resp["error"]

    def test_rejects_bad_sha(self, handler):
        resp = json.loads(handler.handle_control(_start_envelope("x.png", 10, "notahash")))
        assert resp["status"] == "error"

    def test_rejects_concurrent_uploads(self, handler):
        payload = b"x" * 10
        digest = hashlib.sha256(payload).hexdigest()
        first = json.loads(handler.handle_control(_start_envelope("a.png", 10, digest)))
        assert first["status"] == "ok"
        second = json.loads(handler.handle_control(_start_envelope("b.png", 10, digest)))
        assert second["status"] == "error"
        assert "in progress" in second["error"]


class TestHappyPath:
    def test_full_upload_to_update_now(self):
        bridge = FakeBridge()
        handler = UploadHandler(bridge, mtu=64)
        payload = bytes(range(256)) * 4   # 1024 bytes
        digest = hashlib.sha256(payload).hexdigest()

        start_resp = json.loads(handler.handle_control(
            _start_envelope("sunset.png", len(payload), digest, plugin="image_upload", display_now=True)
        ))
        assert start_resp["status"] == "ok"
        upload_id = start_resp["data"]["upload_id"]
        chunk_size = start_resp["data"]["chunk_size"]
        assert chunk_size > 0

        for seq, body in enumerate(_chunk(payload, chunk_size)):
            handler.handle_data(_data_frame(seq, body))

        end_resp = json.loads(handler.handle_control(_end_envelope(upload_id)))
        assert end_resp["status"] == "ok"
        assert end_resp["data"] == {"instance_name": None, "displayed": True}

        assert len(bridge.calls) == 1
        path, kwargs = bridge.calls[0]
        assert path == "/update_now"
        assert kwargs["data"]["plugin_id"] == "image_upload"

    def test_full_upload_to_add_plugin_when_playlist_provided(self):
        bridge = FakeBridge()
        handler = UploadHandler(bridge, mtu=247)
        payload = b"PNGDATA" * 100
        digest = hashlib.sha256(payload).hexdigest()

        start = json.loads(handler.handle_control(_start_envelope(
            "art.png", len(payload), digest,
            plugin="image_upload",
            playlist="Default",
            instance_name="Wall art",
        )))
        upload_id = start["data"]["upload_id"]
        chunk_size = start["data"]["chunk_size"]
        for seq, body in enumerate(_chunk(payload, chunk_size)):
            handler.handle_data(_data_frame(seq, body))
        end = json.loads(handler.handle_control(_end_envelope(upload_id)))

        assert end["status"] == "ok"
        assert end["data"] == {"instance_name": "Wall art", "displayed": False}

        path, kwargs = bridge.calls[0]
        assert path == "/add_plugin"
        assert kwargs["data"]["plugin_id"] == "image_upload"
        refresh = json.loads(kwargs["data"]["refresh_settings"])
        assert refresh["playlist"] == "Default"
        assert refresh["instance_name"] == "Wall art"


class TestErrorCases:
    def test_out_of_order_chunk_rejected(self):
        handler = UploadHandler(FakeBridge(), mtu=247)
        payload = b"y" * 600
        digest = hashlib.sha256(payload).hexdigest()
        json.loads(handler.handle_control(_start_envelope("a.png", 600, digest)))
        with pytest.raises(UploadError):
            handler.handle_data(_data_frame(1, b"y" * 100))   # expected seq=0

    def test_size_mismatch_rejected_on_end(self):
        handler = UploadHandler(FakeBridge(), mtu=247)
        payload = b"z" * 200
        digest = hashlib.sha256(payload).hexdigest()
        start = json.loads(handler.handle_control(_start_envelope("a.png", 200, digest)))
        upload_id = start["data"]["upload_id"]
        # Send only half the bytes.
        handler.handle_data(_data_frame(0, payload[:100]))
        resp = json.loads(handler.handle_control(_end_envelope(upload_id)))
        assert resp["status"] == "error"
        assert "size mismatch" in resp["error"]

    def test_sha_mismatch_rejected(self):
        handler = UploadHandler(FakeBridge(), mtu=247)
        payload = b"hello"
        wrong_digest = "0" * 64
        start = json.loads(handler.handle_control(_start_envelope("a.png", 5, wrong_digest)))
        upload_id = start["data"]["upload_id"]
        handler.handle_data(_data_frame(0, payload))
        resp = json.loads(handler.handle_control(_end_envelope(upload_id)))
        assert resp["status"] == "error"
        assert "sha256 mismatch" in resp["error"]

    def test_data_without_start_rejected(self):
        handler = UploadHandler(FakeBridge(), mtu=247)
        with pytest.raises(UploadError):
            handler.handle_data(_data_frame(0, b"abc"))

    def test_abort_clears_active(self):
        handler = UploadHandler(FakeBridge(), mtu=247)
        payload = b"x" * 50
        digest = hashlib.sha256(payload).hexdigest()
        start = json.loads(handler.handle_control(_start_envelope("a.png", 50, digest)))
        upload_id = start["data"]["upload_id"]
        abort = json.loads(handler.handle_control(json.dumps(
            {"id": "x", "op": "abort", "data": {"upload_id": upload_id}}
        ).encode()))
        assert abort["status"] == "ok"
        # After abort, a new start should succeed.
        again = json.loads(handler.handle_control(_start_envelope("b.png", 50, digest)))
        assert again["status"] == "ok"
