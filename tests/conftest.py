"""Shared pytest configuration.

The application code in ``src/`` uses bare top-level imports (``from ble.X
import Y``, ``from network.X import Z``) because at runtime the
``inkypi``/``inkypi-ble``/``inkypi-netd`` entrypoints add ``src/`` to
``sys.path``. Tests run with the project root on ``sys.path``, so the
package-internal cross-imports would fail without this shim.
"""

from __future__ import annotations

import os
import sys

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
