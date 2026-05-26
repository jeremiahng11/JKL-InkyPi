"""Fragmentation helpers for the RESP characteristic.

BLE notifications can carry at most ``mtu - 3`` bytes. Larger JSON responses
are split into framed fragments:

    [ 1 byte: flags ][ 2 bytes: seq (big-endian) ][ payload bytes ]

The receiver reassembles by concatenating payloads in ``seq`` order until it
sees the ``LAST`` flag (or aborts on ``ERROR``).
"""

from __future__ import annotations

from typing import Iterable, Iterator, List

FRAG_MORE = 0x01
FRAG_LAST = 0x02
FRAG_ERROR = 0x04

HEADER_LEN = 3   # 1 byte flags + 2 byte seq
MAX_SEQ = 0xFFFF


def fragment_payload(payload: bytes, *, mtu: int) -> Iterator[bytes]:
    """Yield framed fragments that fit inside ``mtu`` bytes per notification.

    ``mtu`` is the negotiated ATT MTU. Notifications carry ``mtu - 3`` bytes,
    of which we reserve ``HEADER_LEN`` for our own frame header.
    """
    if mtu < (3 + HEADER_LEN + 1):
        raise ValueError(f"mtu too small for fragmentation: {mtu}")
    body_size = mtu - 3 - HEADER_LEN

    if not payload:
        # Always emit at least one frame so the receiver gets the LAST flag.
        yield _frame(FRAG_LAST, 0, b"")
        return

    total = (len(payload) + body_size - 1) // body_size
    if total > MAX_SEQ + 1:
        raise ValueError(f"payload too large for 16-bit seq: {len(payload)} bytes")

    for seq in range(total):
        chunk = payload[seq * body_size : (seq + 1) * body_size]
        flags = FRAG_LAST if seq == total - 1 else FRAG_MORE
        yield _frame(flags, seq, chunk)


def reassemble_fragments(frames: Iterable[bytes]) -> bytes:
    """Inverse of :func:`fragment_payload`. Useful for tests and any client
    code we ship in the same repo."""
    pieces: List[bytes] = []
    expected_seq = 0
    saw_last = False
    for frame in frames:
        if len(frame) < HEADER_LEN:
            raise ValueError(f"fragment too short: {len(frame)} bytes")
        flags = frame[0]
        seq = int.from_bytes(frame[1:3], "big")
        body = frame[HEADER_LEN:]

        if flags & FRAG_ERROR:
            raise RuntimeError(f"peer signalled error at seq={seq}")
        if seq != expected_seq:
            raise ValueError(f"out-of-order fragment: got {seq}, expected {expected_seq}")
        pieces.append(body)
        expected_seq += 1
        if flags & FRAG_LAST:
            saw_last = True
            break
    if not saw_last:
        raise ValueError("stream ended before LAST flag")
    return b"".join(pieces)


def _frame(flags: int, seq: int, body: bytes) -> bytes:
    if seq < 0 or seq > MAX_SEQ:
        raise ValueError(f"seq out of range: {seq}")
    return bytes([flags & 0xFF]) + seq.to_bytes(2, "big") + body
