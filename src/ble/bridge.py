"""HTTP bridge to the local Flask app.

The BLE handlers don't import any Flask state directly — instead they hit
``http://127.0.0.1:<port>`` so that every code path the web UI uses (with
all its validation, plugin dispatch, and refresh-task wiring) is reused
verbatim. The trade-off is that ``inkypi.service`` must be running for most
operations to succeed; the BLE service stays up either way and reports
``flask_reachable: false`` in INFO when it isn't.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class BridgeResult:
    status_code: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class FlaskBridge:
    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url or os.environ.get("INKYPI_BASE_URL", "http://127.0.0.1:80")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- low level

    def get(self, path: str) -> BridgeResult:
        return self._request("GET", path)

    def post_json(self, path: str, payload: dict) -> BridgeResult:
        return self._request("POST", path, json=payload)

    def post_form(self, path: str, data: dict, files: Optional[dict] = None) -> BridgeResult:
        return self._request("POST", path, data=data, files=files)

    def put_json(self, path: str, payload: dict) -> BridgeResult:
        return self._request("PUT", path, json=payload)

    def put_form(self, path: str, data: dict, files: Optional[dict] = None) -> BridgeResult:
        return self._request("PUT", path, data=data, files=files)

    def delete(self, path: str) -> BridgeResult:
        return self._request("DELETE", path)

    def reachable(self) -> bool:
        try:
            resp = self._client.get("/api/current_image", timeout=2.0)
            # /api/current_image returns 200 or 404 depending on history; both
            # are evidence that the server is up.
            return resp.status_code in (200, 304, 404)
        except httpx.HTTPError:
            return False

    # --------------------------------------------------------------- internals

    def _request(self, method: str, path: str, **kwargs) -> BridgeResult:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("Bridge %s %s failed: %s", method, path, exc)
            return BridgeResult(status_code=599, body={"error": str(exc)})

        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                body: Any = resp.json()
            except json.JSONDecodeError:
                body = resp.text
        else:
            body = resp.text
        return BridgeResult(status_code=resp.status_code, body=body)
