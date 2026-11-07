"""A2687 state tracker tests: reports to the shared model."""

from datetime import UTC, datetime

from ankerprime.model.enums import (
    CableRating, ChargingMode, ChargingProtocol, DisplayLanguage,
    FixedAllocation, OutputId, PortId, ScreenOrientation, ScreenTimeout)
from ankerprime.protocol.a2687.telemetry import decode_report
from ankerprime.protocol.a2687.tlv import typed_bytes, typed_u8
from ankerprime.protocol.a2687.tracker import StateTracker
from tests.protocol.a2687.test_telemetry import (
    APPLE_C1_C2, control_block, port_block, report)


NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def countdown_block(total: int, remaining: int) -> bytes:
    return typed_bytes(b'\x01\x01' + total.to_bytes(4, 'little')
                       + remaining.to_bytes(4, 'little') + b'\x01\x00')


def test_empty_report_changes_nothing() -> None:
    tracker = StateTracker()
    assert not tracker.apply(decode_report(report((0xA1, b'\x21'))), NOW)
    assert tracker.updated_at is None


def test_ports_outputs_and_identities() -> None:
    tracker = StateTracker()
    assert tracker.apply(decode_report(report(
        (0xA5, port_block(1, 20000, 3250, 6500)),
        (0xAC, control_block(0x02, 0x01)),
        (0xA6, port_block(1, 5000, 0, 0)),
        (0xAD, control_block(0x03, 0x02)),
        (0xA7, port_block(0, 0, 0, 0)),
        (0xAE, countdown_block(3600, 1200)),
        (0xB4, APPLE_C1_C2))), NOW)
    state = tracker.build(available=True)
    c1 = state.ports[PortId.C1]
    assert c1.power == 65.0 and c1.delivering
    assert c1.cable == CableRating.EPR
    assert c1.charging_protocol == ChargingProtocol.APPLE_PD
    assert (c1.usb_vendor_id, c1.usb_product_id) == (0x05AC, 0x7309)
    c2 = state.ports[PortId.C2]
    assert c2.cable is None
    assert c2.charging_protocol is None
    assert state.ports[PortId.C3].usb_vendor_id is None
    assert state.outputs[OutputId.C1].is_on
    assert not state.outputs[OutputId.C3].is_on
    countdown = state.outputs[OutputId.C3].countdown
    assert countdown is not None
    assert (countdown.total, countdown.remaining) == (3600, 1200)
    assert countdown.reported_at == NOW
    assert state.outputs[OutputId.C1].countdown is None
    assert state.updated_at == NOW
    assert state.total_power == 65.0


def test_settings_merge_per_field() -> None:
    tracker = StateTracker()
    tracker.apply(decode_report(report(
        (0xA8, typed_u8(4)), (0xA9, typed_u8(80)), (0xAA, typed_u8(0)),
        (0xAF, typed_u8(1)), (0xB2, typed_u8(1)))), NOW)
    tracker.apply(decode_report(report((0xA9, typed_u8(50)))), NOW)
    settings = tracker.build(available=True).settings
    assert settings.charging_mode == ChargingMode.AI
    assert settings.charging_mode_code == 0
    display = settings.display
    assert display.brightness == 50
    assert display.screen_timeout == ScreenTimeout.TWELVE_HOURS
    assert display.orientation == ScreenOrientation.LEFT
    assert display.auto_rotation is True
    assert display.language is None


def test_unreported_settings_keep_written_values() -> None:
    tracker = StateTracker()
    tracker.apply_language(DisplayLanguage.GERMAN)
    tracker.apply_fixed_allocation(FixedAllocation.DUAL_LAPTOP)
    tracker.apply_charging_mode(1)
    settings = tracker.build(available=False).settings
    assert settings.display.language == DisplayLanguage.GERMAN
    assert settings.fixed_allocation == FixedAllocation.DUAL_LAPTOP
    assert settings.charging_mode == ChargingMode.STANDARD


def test_unknown_mode_code_is_kept_raw() -> None:
    tracker = StateTracker()
    tracker.apply(decode_report(report((0xAA, typed_u8(4)))), NOW)
    settings = tracker.build(available=True).settings
    assert settings.charging_mode is None
    assert settings.charging_mode_code == 4
