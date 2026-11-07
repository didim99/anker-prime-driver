"""A2687 report decoding tests, ported from Charker's telemetry tests."""

from ankerprime.model.enums import PortId
from ankerprime.protocol.a2687.telemetry import PortControl, decode_report
from ankerprime.protocol.a2687.tlv import (
    Payload, encode_fields, typed_bytes, typed_u8)


IDLE_CONTROL = bytes.fromhex('01002c010000' '2c010000')
APPLE_C1_C2 = bytes.fromhex('ac050973ac051875fafffbff')
ALL_OCCUPIED = bytes.fromhex('ac051975ac05097300000000')


def port_block(status: int, millivolts: int, milliamps: int,
               centiwatts: int) -> bytes:
    return typed_bytes(bytes((status,)) + millivolts.to_bytes(2, 'little')
                       + milliamps.to_bytes(2, 'little')
                       + centiwatts.to_bytes(2, 'little'))


def control_block(cable: int, profile: int,
                  prefix: bytes = IDLE_CONTROL) -> bytes:
    return typed_bytes(prefix + bytes((cable, profile)))


def report(*fields: tuple[int, bytes]) -> Payload:
    return Payload.parse(b'\x00' + encode_fields(fields))


def test_decodes_three_ports() -> None:
    decoded = decode_report(report(
        (0xA1, b'\x21'),
        (0xA5, port_block(1, 20000, 3250, 6500)),
        (0xA6, port_block(1, 9000, 2000, 1800)),
        (0xA7, port_block(0, 0, 0, 0))))
    c1 = decoded.ports[PortId.C1]
    assert (c1.voltage, c1.current, c1.power) == (20.0, 3.25, 65.0)
    assert c1.is_on
    assert not decoded.ports[PortId.C3].is_on
    assert decoded.settings is None
    assert not decoded.empty


def test_control_block() -> None:
    decoded = decode_report(report(
        (0xA5, port_block(1, 9000, 2200, 1980)),
        (0xAC, control_block(0x02, 0x01))))
    control = decoded.controls[PortId.C1]
    assert (control.cable_code, control.profile_code) == (2, 1)
    assert (control.countdown_total, control.countdown_remaining) == (300,
                                                                      300)
    assert not control.countdown_running


def test_short_control_block_is_dropped() -> None:
    decoded = decode_report(report(
        (0xA5, port_block(1, 9000, 2200, 1980)),
        (0xAC, typed_bytes(bytes.fromhex('00000201')))))
    assert decoded.controls == {}


def test_running_countdown_needs_a_plausible_window() -> None:
    def control(status: int, total: int, remaining: int) -> PortControl:
        block = (bytes((1, status)) + total.to_bytes(4, 'little')
                 + remaining.to_bytes(4, 'little') + b'\x01\x00')
        decoded = PortControl.decode(block)
        assert decoded is not None
        return decoded

    assert control(1, 3600, 1800).countdown_running
    assert not control(0, 3600, 1800).countdown_running
    assert not control(1, 3600, 0).countdown_running
    assert not control(1, 3600, 3601).countdown_running
    assert not control(1, 90_000, 100).countdown_running


def test_payload_without_ports_or_settings_is_empty() -> None:
    assert decode_report(report((0xA1, b'\x21'))).empty
    assert decode_report(report(
        (0xA5, typed_bytes(b'\x01\x20\x4e')))).empty


def test_connected_devices() -> None:
    decoded = decode_report(report(
        (0xA5, port_block(1, 9000, 2000, 1800)), (0xB4, APPLE_C1_C2)))
    c1 = decoded.identities[PortId.C1]
    assert (c1.vendor_id, c1.product_id) == (0x05AC, 0x7309)
    assert decoded.identities[PortId.C3].empty
    occupied = decode_report(report((0xB4, typed_bytes(ALL_OCCUPIED))))
    assert occupied.identities[PortId.C3].empty
    assert not occupied.identities[PortId.C1].empty
    short = decode_report(report((0xB4, APPLE_C1_C2[:8])))
    assert list(short.identities) == [PortId.C1, PortId.C2]


def test_settings_snapshot() -> None:
    settings = decode_report(report(
        (0xA8, typed_u8(1)), (0xA9, typed_u8(0x50)), (0xAA, typed_u8(1)),
        (0xAF, typed_u8(3)), (0xB2, typed_u8(1)))).settings
    assert settings is not None
    assert (settings.screen_timeout, settings.brightness,
            settings.charging_mode, settings.orientation,
            settings.auto_rotation) == (1, 80, 1, 3, True)
    off = decode_report(report((0xB2, typed_u8(0)))).settings
    assert off is not None and off.auto_rotation is False


def test_setting_shapes_and_ranges() -> None:
    for raw in (b'\x50', typed_bytes(b'\x50'), typed_u8(0x50)):
        settings = decode_report(report((0xA9, raw))).settings
        assert settings is not None and settings.brightness == 80
    settings = decode_report(report(
        (0xA9, typed_u8(0xFF)), (0xA8, typed_u8(5)), (0xAF, typed_u8(4)),
        (0xB2, typed_u8(2)), (0xAA, typed_u8(4)))).settings
    assert settings is not None
    assert settings.brightness is None
    assert settings.screen_timeout is None
    assert settings.orientation is None
    assert settings.auto_rotation is None
    assert settings.charging_mode == 4
    wide = decode_report(report((0xA9, typed_bytes(b'\x50\x00')))).settings
    assert wide is not None and wide.brightness is None
