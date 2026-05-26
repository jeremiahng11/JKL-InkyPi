"""Handler for the UPC (control) and UPD (data) characteristics.

Stateful: a single client can have one in-flight upload at a time. The
handler buffers chunks in memory until ``end`` arrives, verifies the SHA-256
digest, then posts the file to the appropriate Flask endpoint via the
bridge.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ble.bridge import FlaskBridge
from ble.gatt import ASSUMED_MTU

logger = logging.getLogger(__name__)

OP_DATA = 0x02

MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB hard ceiling; e-ink images are tiny


class UploadError(RuntimeError):
    pass


@dataclass
class _Upload:
    upload_id: str
    name: str
    size: int
    sha256: str
    plugin: str
    playlist: Optional[str] = None
    instance_name: Optional[str] = None
    display_now: bool = False
    chunk_size: int = 0
    next_seq: int = 0
    buffer: io.BytesIO = field(default_factory=io.BytesIO)


class UploadHandler:
    def __init__(self, bridge: FlaskBridge, *, mtu: int = ASSUMED_MTU) -> None:
        self.bridge = bridge
        self.mtu = mtu
        self._active: Optional[_Upload] = None

    # ----------------------------------------------------------------- UPC

    def handle_control(self, raw: bytes) -> bytes:
        request_id = ""
        try:
            envelope = json.loads(raw.decode("utf-8"))
            request_id = str(envelope.get("id", ""))
            op = envelope.get("op")
            data = envelope.get("data") or {}
            if op == "start":
                result = self._start(data)
            elif op == "end":
                result = self._end(data)
            elif op == "abort":
                result = self._abort(data)
            else:
                raise UploadError(f"unknown upload op: {op}")
            response = {"id": request_id, "status": "ok", "data": result}
        except UploadError as exc:
            response = {"id": request_id, "status": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception("Upload control crash on raw=%r", raw[:128])
            response = {"id": request_id, "status": "error", "error": f"internal: {exc}"}
        return json.dumps(response, separators=(",", ":")).encode("utf-8")

    # ----------------------------------------------------------------- UPD

    def handle_data(self, raw: bytes) -> None:
        """Consume a single UPD frame. Raises :class:`UploadError` on framing
        problems; the BLE service forwards those to the client via UPC as an
        error envelope."""
        if not self._active:
            raise UploadError("no active upload")
        if len(raw) < 5:
            raise UploadError(f"chunk too short: {len(raw)} bytes")
        op = raw[0]
        if op != OP_DATA:
            raise UploadError(f"unexpected chunk op: {op:#x}")
        seq = int.from_bytes(raw[1:5], "big")
        body = raw[5:]
        if seq != self._active.next_seq:
            raise UploadError(
                f"out-of-order chunk: got {seq}, expected {self._active.next_seq}"
            )
        if self._active.buffer.tell() + len(body) > self._active.size:
            raise UploadError("chunk would exceed declared size")
        self._active.buffer.write(body)
        self._active.next_seq += 1

    # --------------------------------------------------------------- internals

    def _start(self, data: dict) -> dict:
        if self._active is not None:
            raise UploadError("an upload is already in progress; send 'abort' first")

        name = data.get("name")
        size = data.get("size")
        sha256 = (data.get("sha256") or "").lower()
        plugin = data.get("plugin", "image_upload")

        if not isinstance(name, str) or not name:
            raise UploadError("name is required")
        if not isinstance(size, int) or size <= 0:
            raise UploadError("size must be a positive integer")
        if size > MAX_UPLOAD_BYTES:
            raise UploadError(f"size {size} exceeds maximum {MAX_UPLOAD_BYTES}")
        if not sha256 or len(sha256) != 64:
            raise UploadError("sha256 (hex, 64 chars) is required")

        upload_id = secrets.token_hex(8)
        chunk_size = max(20, self.mtu - 3 - 5)   # 3 byte ATT header + 5 byte UPD header

        self._active = _Upload(
            upload_id=upload_id,
            name=name,
            size=size,
            sha256=sha256,
            plugin=plugin,
            playlist=data.get("playlist") or None,
            instance_name=data.get("instance_name") or None,
            display_now=bool(data.get("display_now", False)),
            chunk_size=chunk_size,
        )
        logger.info(
            "Upload started: id=%s name=%s size=%d plugin=%s playlist=%s",
            upload_id, name, size, plugin, self._active.playlist,
        )
        return {"upload_id": upload_id, "chunk_size": chunk_size}

    def _end(self, data: dict) -> dict:
        upload = self._require_active(data)
        body = upload.buffer.getvalue()

        if len(body) != upload.size:
            self._active = None
            raise UploadError(f"size mismatch: declared {upload.size}, received {len(body)}")
        actual = hashlib.sha256(body).hexdigest()
        if actual != upload.sha256:
            self._active = None
            raise UploadError(f"sha256 mismatch: declared {upload.sha256}, computed {actual}")

        try:
            result = self._bridge_upload(upload, body)
        finally:
            self._active = None
        return result

    def _abort(self, data: dict) -> dict:
        upload = self._require_active(data)
        self._active = None
        logger.info("Upload aborted: id=%s", upload.upload_id)
        return {"aborted": upload.upload_id}

    def _require_active(self, data: dict) -> _Upload:
        if self._active is None:
            raise UploadError("no active upload")
        upload_id = data.get("upload_id")
        if upload_id and upload_id != self._active.upload_id:
            raise UploadError("upload_id does not match active upload")
        return self._active

    def _bridge_upload(self, upload: _Upload, body: bytes) -> dict:
        """Push the assembled image into Flask.

        If a playlist + instance name are present we route through
        ``/add_plugin`` (matching the web UI's "add plugin instance" flow);
        otherwise we go straight to ``/update_now`` which pushes the image to
        the display immediately without persisting it.
        """
        files = {"image": (upload.name, body, "application/octet-stream")}

        if upload.playlist and upload.instance_name:
            refresh_settings = json.dumps({
                "playlist":     upload.playlist,
                "instance_name": upload.instance_name,
                "refreshType":  "interval",
                "unit":         "hour",
                "interval":     "1",
            })
            form = {
                "plugin_id":        upload.plugin,
                "refresh_settings": refresh_settings,
            }
            result = self.bridge.post_form("/add_plugin", data=form, files=files)
            if not result.ok:
                raise UploadError(self._format_bridge_error(result))
            return {"instance_name": upload.instance_name, "displayed": False}

        form = {"plugin_id": upload.plugin}
        result = self.bridge.post_form("/update_now", data=form, files=files)
        if not result.ok:
            raise UploadError(self._format_bridge_error(result))
        return {"instance_name": None, "displayed": True}

    @staticmethod
    def _format_bridge_error(result) -> str:
        if isinstance(result.body, dict) and "error" in result.body:
            return str(result.body["error"])
        return f"bridge returned HTTP {result.status_code}"
