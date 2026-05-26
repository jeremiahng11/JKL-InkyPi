"""BLE peripheral for InkyPi.

The package exposes a GATT server (see :mod:`ble.service`) that bridges
phone-app commands to the existing Flask web API and to direct
NetworkManager calls for Wi-Fi provisioning. See ``docs/bluetooth.md`` for
the wire protocol.
"""

from ble.gatt import CHAR_UUIDS, SERVICE_UUID
from ble.framing import (
    FRAG_ERROR,
    FRAG_LAST,
    FRAG_MORE,
    fragment_payload,
    reassemble_fragments,
)

__all__ = [
    "CHAR_UUIDS",
    "FRAG_ERROR",
    "FRAG_LAST",
    "FRAG_MORE",
    "SERVICE_UUID",
    "fragment_payload",
    "reassemble_fragments",
]
