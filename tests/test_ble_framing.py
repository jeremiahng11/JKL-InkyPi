"""Round-trip tests for the BLE response fragmentation helper."""

import pytest

from ble.framing import (
    FRAG_LAST,
    FRAG_MORE,
    fragment_payload,
    reassemble_fragments,
)


class TestFragmentPayload:
    @pytest.mark.parametrize("payload_size", [0, 1, 50, 200, 1000, 4096])
    @pytest.mark.parametrize("mtu", [23, 100, 247, 512])
    def test_roundtrip(self, payload_size: int, mtu: int) -> None:
        payload = bytes(i % 256 for i in range(payload_size))
        frames = list(fragment_payload(payload, mtu=mtu))
        assert reassemble_fragments(frames) == payload

    def test_empty_payload_emits_single_last_frame(self) -> None:
        frames = list(fragment_payload(b"", mtu=100))
        assert len(frames) == 1
        assert frames[0][0] & FRAG_LAST
        assert not frames[0][0] & FRAG_MORE

    def test_last_frame_marked_last(self) -> None:
        payload = b"x" * 500
        frames = list(fragment_payload(payload, mtu=50))
        for frame in frames[:-1]:
            assert frame[0] & FRAG_MORE
            assert not frame[0] & FRAG_LAST
        assert frames[-1][0] & FRAG_LAST
        assert not frames[-1][0] & FRAG_MORE

    def test_seq_is_monotonic(self) -> None:
        payload = b"abcdefghij" * 100
        frames = list(fragment_payload(payload, mtu=40))
        seqs = [int.from_bytes(f[1:3], "big") for f in frames]
        assert seqs == list(range(len(frames)))

    def test_mtu_too_small_raises(self) -> None:
        with pytest.raises(ValueError):
            list(fragment_payload(b"hello", mtu=5))

    def test_each_frame_fits_in_mtu(self) -> None:
        payload = b"y" * 2000
        mtu = 100
        for frame in fragment_payload(payload, mtu=mtu):
            # frame is the *body* of the notification; the 3-byte ATT header
            # is added by the transport. So the frame itself must fit in
            # mtu - 3.
            assert len(frame) <= mtu - 3


class TestReassembleFragments:
    def test_rejects_out_of_order(self) -> None:
        frames = list(fragment_payload(b"hello world" * 20, mtu=40))
        with pytest.raises(ValueError):
            reassemble_fragments([frames[1], frames[0], *frames[2:]])

    def test_rejects_missing_last_flag(self) -> None:
        frames = list(fragment_payload(b"x" * 200, mtu=40))
        # Strip LAST flag from the final frame.
        last = frames[-1]
        frames[-1] = bytes([last[0] & ~FRAG_LAST | FRAG_MORE]) + last[1:]
        with pytest.raises(ValueError):
            reassemble_fragments(frames)
