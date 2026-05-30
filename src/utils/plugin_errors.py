"""In-process ring buffer of recent plugin render failures.

When a plugin's `generate_image()` throws inside RefreshTask, the
display silently keeps showing the last successful frame. Users get
no signal that anything went wrong unless they read the journal.
This module records each failure (newest-first, last N per plugin)
so the companion app can surface "this plugin has been failing"
right where the user sees the plugin.

Stored in memory only — restarting inkypi.service drops the buffer,
which is fine: the use case is "I noticed the weather hasn't
updated in an hour, what's wrong?", not long-term audit. Persisting
would mean cleaning up on plugin uninstall, dealing with disk
growth, etc. — out of scope.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Keep the last N failures per plugin. 20 is enough to cover a few
# refresh cycles of broken behaviour without unbounded growth.
_RING_SIZE = 20

_LOCK = threading.Lock()
_BUFFERS: dict[str, deque] = {}


def record(plugin_id: str, message: str, error_type: Optional[str] = None) -> None:
    """Append one error entry for `plugin_id`. Safe to call from any thread."""
    if not plugin_id:
        return
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "type": error_type or "Error",
        # Keep messages bounded — exception strings can occasionally be
        # very long (stack-like; multi-line tracebacks) and we render
        # this in a phone UI.
        "message": (message or "")[:500],
    }
    with _LOCK:
        buf = _BUFFERS.get(plugin_id)
        if buf is None:
            buf = deque(maxlen=_RING_SIZE)
            _BUFFERS[plugin_id] = buf
        buf.append(entry)


def get(plugin_id: str) -> list[dict]:
    """Return recent errors for one plugin, newest first."""
    with _LOCK:
        buf = _BUFFERS.get(plugin_id)
        if not buf:
            return []
        return list(reversed(buf))


def get_all() -> dict[str, list[dict]]:
    """Return {plugin_id: [errors, newest_first]} for every plugin with errors."""
    with _LOCK:
        return {
            pid: list(reversed(buf))
            for pid, buf in _BUFFERS.items()
            if buf
        }


def clear(plugin_id: Optional[str] = None) -> None:
    """Drop the buffer for one plugin, or all if `plugin_id` is None."""
    with _LOCK:
        if plugin_id is None:
            _BUFFERS.clear()
        else:
            _BUFFERS.pop(plugin_id, None)
