"""Thin wrapper around ``nmcli``. Centralises subprocess handling so the
higher-level wifi / hotspot modules stay focused on intent."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

NMCLI_BIN = shutil.which("nmcli") or "/usr/bin/nmcli"
DEFAULT_TIMEOUT = 30


class NmcliError(RuntimeError):
    """Raised when nmcli exits non-zero or is unavailable."""


@dataclass
class NmcliResult:
    stdout: str
    stderr: str
    returncode: int


def run(args: Sequence[str], *, timeout: int = DEFAULT_TIMEOUT, check: bool = True) -> NmcliResult:
    """Invoke nmcli with the given arguments and return its output.

    Use ``check=False`` for commands where a non-zero exit code is expected
    (e.g. ``con show <name>`` when probing for existence).
    """
    cmd = [NMCLI_BIN, *args]
    logger.debug("nmcli %s", " ".join(args))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise NmcliError("nmcli is not installed — install NetworkManager") from exc
    except subprocess.TimeoutExpired as exc:
        raise NmcliError(f"nmcli timed out after {timeout}s: {' '.join(args)}") from exc

    result = NmcliResult(
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        returncode=completed.returncode,
    )
    if check and completed.returncode != 0:
        raise NmcliError(
            f"nmcli {' '.join(args)} failed (exit {result.returncode}): {result.stderr}"
        )
    return result


def terse(args: Sequence[str], fields: Sequence[str], **kw) -> list[list[str]]:
    """Run nmcli in terse mode and return a list of split rows.

    ``-t -f <fields>`` produces colon-separated output where colons inside
    field values are escaped as ``\\:``. This helper undoes that escaping.
    """
    full = ["-t", "-f", ",".join(fields), *args]
    out = run(full, **kw).stdout
    rows: list[list[str]] = []
    for line in out.splitlines():
        if not line:
            continue
        # nmcli escapes ':' inside values as '\:'. Split on unescaped ':'.
        parts: list[str] = []
        buf: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                buf.append(line[i + 1])
                i += 2
                continue
            if ch == ":":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        parts.append("".join(buf))
        rows.append(parts)
    return rows
