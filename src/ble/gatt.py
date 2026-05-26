"""GATT service / characteristic identifiers for the InkyPi BLE protocol.

Keep this file authoritative — the Flutter app and any other client should
mirror these constants exactly. The values are documented in
``docs/bluetooth.md``.
"""

from __future__ import annotations

SERVICE_UUID = "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0000"

CHAR_UUIDS = {
    "info": "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0001",
    "cmd":  "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0002",
    "resp": "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0003",
    "wifi": "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0004",
    "upc":  "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0005",
    "upd":  "4e6b9c80-1d3a-4e8a-9b7e-1f7b3c5a0006",
}

# Default ATT MTU when we cannot negotiate higher. Used to compute the
# default chunk size advertised in upload start responses.
DEFAULT_ATT_MTU = 23   # spec minimum; effective payload is MTU - 3
ASSUMED_MTU = 185      # iOS default after negotiation
