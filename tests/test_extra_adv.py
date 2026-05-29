"""Pure-function tests for the BLE-advertisement manufacturer-data
encoder.

The wire format is the contract between this module and the companion
app's `decodeInkyPiAdvertisedAddress` in `lib/ble/gatt.dart`. Test
both legal and illegal inputs so the bytes-going-out side never drifts
from the bytes-coming-in side on the phone.
"""

import pytest

from ble.extra_adv import (
    INKYPI_ADV_VERSION,
    _pack_manufacturer_data,
)


class TestPackManufacturerData:
    def test_client_mode_packs_six_bytes(self) -> None:
        payload = _pack_manufacturer_data("client", "192.168.0.246")
        assert payload == bytes([INKYPI_ADV_VERSION, 0x01, 192, 168, 0, 246])

    def test_ap_mode_uses_mode_byte_2(self) -> None:
        payload = _pack_manufacturer_data("ap", "192.168.4.1")
        assert payload == bytes([INKYPI_ADV_VERSION, 0x02, 192, 168, 4, 1])

    def test_offline_mode_returns_none(self) -> None:
        # We never want to advertise an IP we won't actually be
        # reachable at — the app would tap a Wi-Fi tile that
        # guarantee-fails the probe.
        assert _pack_manufacturer_data("offline", "192.168.0.246") is None

    def test_unknown_mode_returns_none(self) -> None:
        assert _pack_manufacturer_data("weird", "192.168.0.246") is None

    def test_none_mode_returns_none(self) -> None:
        assert _pack_manufacturer_data(None, "192.168.0.246") is None

    def test_missing_ip_returns_none(self) -> None:
        assert _pack_manufacturer_data("client", None) is None
        assert _pack_manufacturer_data("client", "") is None

    @pytest.mark.parametrize("ip", [
        "192.168",            # too few octets
        "192.168.0",
        "192.168.0.246.1",    # too many octets
        "192.168.0.300",      # out of range
        "192.168.0.-1",
        "not.an.ip.address",  # non-numeric
        "192.168.0",
    ])
    def test_malformed_ip_returns_none(self, ip: str) -> None:
        assert _pack_manufacturer_data("client", ip) is None

    def test_version_byte_is_always_first(self) -> None:
        payload = _pack_manufacturer_data("client", "10.0.0.1")
        assert payload is not None
        assert payload[0] == INKYPI_ADV_VERSION

    def test_payload_length_is_six(self) -> None:
        payload = _pack_manufacturer_data("client", "10.0.0.1")
        assert payload is not None
        assert len(payload) == 6

    @pytest.mark.parametrize("ip", [
        "0.0.0.0",
        "255.255.255.255",
        "10.0.0.1",
        "172.16.45.99",
    ])
    def test_round_trip_consistency(self, ip: str) -> None:
        # The decoded ip-string should match the input verbatim.
        payload = _pack_manufacturer_data("client", ip)
        assert payload is not None
        decoded_ip = ".".join(str(o) for o in payload[2:6])
        assert decoded_ip == ip
