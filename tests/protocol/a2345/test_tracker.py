"""A2345 state tracking and model mapping tests."""

from datetime import UTC, datetime, timedelta

import pytest

from ankerprime.model import (
    A2345, CableRating, Capability, ChargingMode, ChargingProtocol,
    ClockTheme, DeviceBrand, DeviceInfo, HourFormat, KnobDirection,
    OutputId, PortId, Schedule, ScreenTimeout, Weekday)
from ankerprime.protocol.a2345.frame import Tlv, decode_frame
from ankerprime.protocol.a2345.messages import UnknownMessage, decode_message
from ankerprime.protocol.a2345.settings import SettingField
from ankerprime.protocol.a2345.tracker import StateTracker
from ankerprime.protocol.a2345.updates import (
    ChargingModeUpdate, CountdownUpdate,
    OutputSwitchUpdate, SettingsChange, ValueUpdate)
from tests.protocol.a2345.fixtures import (
    REALTIME_CHINA_FIRMWARE, REALTIME_OLD_FIRMWARE,
    STATUS_SNAPSHOT, VERSION_INFO)


T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def tracker_with(*frames: bytes) -> StateTracker:
    tracker = StateTracker()
    for index, data in enumerate(frames):
        message = decode_message(decode_frame(data))
        tracker.apply(message, T0 + timedelta(seconds=index))
    return tracker


def test_empty_tracker_builds_default_state() -> None:
    state = StateTracker().build(available=False)
    assert state.product is A2345
    assert not state.ports and not state.outputs
    assert state.capabilities == A2345.capabilities
    assert state.updated_at is None


def test_realtime_ports_map_to_model() -> None:
    state = tracker_with(REALTIME_OLD_FIRMWARE).build(available=True)
    c1 = state.ports[PortId.C1]
    assert c1.active and c1.delivering
    assert c1.voltage == pytest.approx(14.984)
    assert c1.power == pytest.approx(23.84)
    assert c1.cable is None
    assert len(state.ports) == 6
    assert state.available
    assert state.updated_at == T0


def test_mainland_port_extras_map_to_model() -> None:
    c1 = tracker_with(REALTIME_CHINA_FIRMWARE).build(
        available=True).ports[PortId.C1]
    assert c1.cable == CableRating.FIVE_AMP
    assert c1.charging_protocol == ChargingProtocol.HUAWEI_SCP
    assert c1.device_brand == DeviceBrand.HUAWEI_HONOR


def test_snapshot_maps_outputs_and_settings() -> None:
    state = tracker_with(STATUS_SNAPSHOT).build(available=True)
    assert set(state.outputs) == set(OutputId)
    c1 = state.outputs[OutputId.C1]
    assert c1.is_on is False
    assert c1.power_on_schedule is None
    assert c1.countdown is None
    assert c1.preferred is None
    c4 = state.outputs[OutputId.C4]
    assert c4.power_on_schedule == Schedule(False, 8, 0, Weekday.TUESDAY)
    assert c4.power_off_schedule == Schedule(False, 23, 0, Weekday.TUESDAY)

    settings = state.settings
    assert settings.charging_mode == ChargingMode.CUSTOM
    assert settings.charging_mode_code == 5
    assert settings.max_compatibility is False
    assert settings.updating_firmware is False
    assert settings.display.brightness == 20
    assert settings.display.screen_timeout == ScreenTimeout.THIRTY_SECONDS
    assert settings.display.knob_direction == KnobDirection.BACKWARD
    assert settings.display.hour_format == HourFormat.TWENTY_FOUR_HOUR
    clock = settings.clock
    assert clock is not None and not clock.enabled
    assert clock.theme == ClockTheme.STYLE_2
    assert clock.schedule is not None and clock.schedule.end_hour == 23
    assert state.over_temperature is False and state.error_code == 0
    assert state.info.firmware_version == '2.1.1.2'


def test_firmware_bits_narrow_capabilities() -> None:
    state = tracker_with(STATUS_SNAPSHOT).build(available=True)
    assert not state.supports(Capability.UFCS)
    assert Capability.UFCS not in state.capabilities
    assert state.supports(Capability.MAX_COMPATIBILITY)
    assert state.supports(Capability.CLOCK_SCHEDULE)
    assert state.supports(Capability.OUTPUT_SWITCH)


def test_countdown_and_priority_map_to_model() -> None:
    tracker = tracker_with(STATUS_SNAPSHOT)
    later = T0 + timedelta(minutes=5)
    countdown_update = CountdownUpdate(OutputId.C2, True, 3600, 3000)
    tracker.apply(SettingsChange(0x0307, [countdown_update]), later)
    mode_update = ChargingModeUpdate(2, priority=1)
    tracker.apply(SettingsChange(0x030D, [mode_update]), later)
    state = tracker.build(available=True)
    countdown = state.outputs[OutputId.C2].countdown
    assert countdown is not None
    assert countdown.ends_at == later + timedelta(seconds=3000)
    assert state.outputs[OutputId.C1].preferred is True
    assert state.outputs[OutputId.C2].preferred is False
    assert state.outputs[OutputId.USB_A].preferred is None
    assert state.settings.charging_mode == ChargingMode.PORT_PRIORITY


def test_fault_codes_map_to_outputs() -> None:
    tracker = tracker_with(STATUS_SNAPSHOT)
    tracker.apply(SettingsChange(0x0304, [
        ValueUpdate(SettingField.ERROR_CODE, 0b100000)]), T0)
    state = tracker.build(available=True)
    assert state.outputs[OutputId.USB_A].fault
    assert not state.outputs[OutputId.C1].fault
    assert state.over_temperature is False
    assert state.error_code == 0b100000


def test_change_before_snapshot_is_kept() -> None:
    tracker = StateTracker()
    change = SettingsChange(0x030A, [ValueUpdate(SettingField.BRIGHTNESS, 60)])
    assert tracker.apply(change, T0)
    assert tracker.build(available=True).settings.display.brightness == 60


def test_switch_change_updates_output() -> None:
    tracker = tracker_with(STATUS_SNAPSHOT)
    change = SettingsChange(0x0302, [OutputSwitchUpdate(OutputId.C1, True)])
    tracker.apply(change, T0)
    assert tracker.build(available=True).outputs[OutputId.C1].is_on is True


def test_version_info_fills_device_info() -> None:
    tracker = tracker_with(VERSION_INFO)
    base = DeviceInfo(serial='SN', name='Desk charger')
    info = tracker.build(info=base, available=True).info
    assert (info.serial, info.name) == ('SN', 'Desk charger')
    assert info.firmware_version == '2.1.1.6'
    assert info.hardware_version == '1.0.0'


def test_snapshot_firmware_wins_over_version_message() -> None:
    info = tracker_with(VERSION_INFO, STATUS_SNAPSHOT).build(
        available=True).info
    assert info.firmware_version == '2.1.1.2'


def test_messages_without_state_are_ignored() -> None:
    tracker = StateTracker()
    frame = decode_frame(VERSION_INFO)
    assert not tracker.apply(UnknownMessage(frame), T0)
    assert not tracker.apply(SettingsChange(0x0302, []), T0)
    assert tracker.updated_at is None


def test_energy_is_passed_through() -> None:
    state = tracker_with(REALTIME_OLD_FIRMWARE).build(
        available=True, energy={PortId.C1: 1.5})
    assert state.energy[PortId.C1] == 1.5


def test_unknown_mode_code_keeps_raw_value() -> None:
    tracker = StateTracker()
    tracker.apply(SettingsChange(0x030D, [ChargingModeUpdate(9)]), T0)
    settings = tracker.build(available=True).settings
    assert settings.charging_mode is None
    assert settings.charging_mode_code == 9


def test_usb_identity_maps_to_port() -> None:
    snapshot = decode_frame(STATUS_SNAPSHOT)
    ids = bytes.fromhex('ac05a812') + bytes.fromhex('fafffbff') * 3
    fields = [Tlv(field.tag, b'\x04' + ids) if field.tag == 0xBD else field
              for field in snapshot.fields]
    frame = type(snapshot)(version=3, speaker=1, group=15,
                           message_type=0x0A00, increment=0, fields=fields,
                           length=snapshot.length)
    tracker = StateTracker()
    tracker.apply(decode_message(frame), T0)
    state = tracker.build(available=True)
    assert state.ports[PortId.C1].usb_vendor_id == 0x05AC
    assert state.ports[PortId.C1].usb_product_id == 0x12A8
    assert state.ports[PortId.C2].usb_vendor_id is None
