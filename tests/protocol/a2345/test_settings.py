"""A2345 settings, change message and write acknowledgement tests."""

import pytest

from ankerprime.model import OutputId, ScheduleSlot
from ankerprime.protocol.a2345.device_settings import DeviceSettings
from ankerprime.protocol.a2345.frame import Frame, Message, Tlv, decode_frame
from ankerprime.protocol.a2345.messages import (
    StatusSnapshot, UnknownMessage, decode_message, decode_version_info)
from ankerprime.protocol.a2345.settings import (
    Capabilities, ClockState, ClockWindow, CustomProfile,
    PortProtocol, PortSchedule, SettingField)
from ankerprime.protocol.a2345.updates import (
    ChargingModeUpdate, ClockUpdate, CountdownUpdate,
    OutputSwitchUpdate, PriorityUpdate, ScheduleUpdate, SettingsChange,
    ThemeDownloadUpdate, ValueUpdate, WriteAcknowledgement)
from tests.protocol.a2345.fixtures import (
    OUTPUT_SWITCH_ACK, OUTPUT_SWITCH_PUSH, REALTIME_ACKNOWLEDGEMENT,
    STATUS_SNAPSHOT, THEME_STATUS, VERSION_INFO, VERSION_INFO_UNTYPED)


def change_frame(message_type: int, fields: list[Tlv]) -> Frame:
    return Frame(version=3, speaker=1, group=15, message_type=message_type,
                 increment=None, fields=[Tlv(0xA1, b'\x64'), *fields],
                 length=0)


def decode_updates(message_type: int, fields: list[Tlv]) -> tuple[object, ...]:
    message = decode_message(change_frame(message_type, fields))
    assert isinstance(message, SettingsChange)
    return message.updates


def snapshot_settings() -> DeviceSettings:
    message = decode_message(decode_frame(STATUS_SNAPSHOT))
    assert isinstance(message, StatusSnapshot)
    return message.settings


def test_decodes_captured_snapshot_settings() -> None:
    settings = snapshot_settings()
    assert settings.firmware_version == '2.1.1.2'
    assert settings.error_code == 0
    assert not settings.has_fault
    assert len(settings.outputs) == 5

    c1 = settings.outputs[OutputId.C1]
    assert not c1.is_on
    assert c1.power_on.unset and c1.power_on.hour == 12
    assert c1.countdown_total == 2100
    assert c1.priority == 0

    c4 = settings.outputs[OutputId.C4]
    assert c4.power_on == PortSchedule(0, 8, 0, 2)
    assert c4.power_off == PortSchedule(0, 23, 0, 2)
    assert c4.countdown_total == 600

    assert settings.clock == ClockState(False, False, 1)
    assert settings.screen_timeout == 1
    assert settings.charging_mode == 5 and settings.custom_charging_mode
    assert settings.updating_firmware is False
    assert settings.brightness == 20
    assert settings.knob_direction == 1
    assert settings.hour_format == 1
    assert settings.protocol_version == 1


def test_decodes_captured_snapshot_gated_settings() -> None:
    settings = snapshot_settings()
    assert settings.capabilities == Capabilities(0x02FF)
    assert settings.supports(Capabilities.MAX_COMPATIBILITY)
    assert not settings.supports(Capabilities.UFCS_SWITCH)
    assert settings.custom_profile == CustomProfile(1, False, (15,) * 5)
    assert settings.clock_schedule == ClockWindow(8, 0, 23, 0, 0x7F)
    both = PortProtocol.SCP | PortProtocol.UFCS
    assert settings.port_protocols == (both,) * 4
    assert settings.max_compatibility is False
    # BC is present but its capability bit is clear, as the official app reads it.
    assert settings.ufcs is None
    # The bit is set but this firmware sends no BF.
    assert settings.device_identification is None
    assert len(settings.usb_identities) == 4
    assert all(slot.empty for slot in settings.usb_identities)


def test_decodes_captured_theme_status() -> None:
    updates = decode_message(decode_frame(THEME_STATUS))
    expected = ClockState(False, False, 1, showing_holiday=False)
    assert updates == SettingsChange(0x0A02, [ClockUpdate(expected, True)])


def test_decodes_captured_switch_push_and_acks() -> None:
    push = decode_message(decode_frame(OUTPUT_SWITCH_PUSH))
    assert push == SettingsChange(0x0302, [OutputSwitchUpdate(OutputId.C1, True)])
    ack = decode_message(decode_frame(OUTPUT_SWITCH_ACK))
    assert ack == WriteAcknowledgement(0x0A07, 0, output=OutputId.C1)
    short = decode_message(decode_frame(REALTIME_ACKNOWLEDGEMENT))
    assert short == WriteAcknowledgement(0x0A0B, 0)


def test_schedule_acknowledgement_echoes_output_and_slot() -> None:
    frame = Frame(version=3, speaker=1, group=15, message_type=0x0A08,
                  increment=0, fields=[Tlv(0xA1, b'\x34'), Tlv(0xA2, b'\x01\x01'),
                                       Tlv(0xA3, b'\x01\x01')], length=0)
    ack = decode_message(frame)
    assert ack == WriteAcknowledgement(0x0A08, 0, output=OutputId.C2,
                                       slot=ScheduleSlot.POWER_ON)
    assert isinstance(ack, WriteAcknowledgement) and not ack.rejected


def test_rejected_acknowledgement() -> None:
    frame = Frame(version=3, speaker=1, group=15, message_type=0x0A04,
                  increment=4, fields=[Tlv(0xA1, b'\x34')], length=0)
    ack = decode_message(frame)
    assert isinstance(ack, WriteAcknowledgement) and ack.rejected


def test_decodes_untyped_version_frame() -> None:
    version = decode_version_info(decode_frame(VERSION_INFO_UNTYPED))
    assert version.hardware_version == 'v0.2.9.7'
    assert version.software_version == '2.1.1.2'
    assert version.product_code == 'A2345'


@pytest.mark.parametrize(('message_type', 'fields', 'expected'), [
    (0x0307, [Tlv(0xA2, b'\x01\x04'),
              Tlv(0xA3, bytes((4, 1, 0x84, 3, 0, 0, 0x58, 2, 0, 0)))],
     CountdownUpdate(OutputId.USB_A, True, 900, 600)),
    (0x0308, [Tlv(0xA2, b'\x01\x00'), Tlv(0xA3, b'\x01\x01'),
              Tlv(0xA4, bytes((3, 1, 8, 0, 0x1F)))],
     ScheduleUpdate(OutputId.C1, ScheduleSlot.POWER_ON,
                    PortSchedule(1, 8, 0, 0x1F))),
    (0x030D, [Tlv(0xA2, b'\x01\x05'),
              Tlv(0xA3, bytes((4, 1, 0, 15, 15, 15, 15, 15))),
              Tlv(0xA4, b'\x01\x03')],
     ChargingModeUpdate(5, CustomProfile(1, False, (15,) * 5), 3)),
    (0x0309, [Tlv(0xA2, b'\x01\x13')],
     ValueUpdate(SettingField.SCREEN_TIMEOUT, 3)),
    (0x030A, [Tlv(0xA2, b'\x01\x50')], ValueUpdate(SettingField.BRIGHTNESS, 80)),
    (0x0304, [Tlv(0xA2, b'\x02\x04\x00')],
     ValueUpdate(SettingField.ERROR_CODE, 4)),
    (0x0311, [Tlv(0xA2, bytes((3, 9, 0, 0, 0))), Tlv(0xA3, b'\x01\x01'),
              Tlv(0xA4, b'\x01\x03')],
     ThemeDownloadUpdate(9, 1, 3)),
    (0x030F, [Tlv(0xA3, b'\x01\x05')], PriorityUpdate(bitmap=5)),
    (0x030F, [Tlv(0xA2, b'\x04\x02\x01')],
     PriorityUpdate(output=OutputId.C3, preferred=True)),
])
def test_decodes_change_messages(
        message_type: int, fields: list[Tlv], expected: object) -> None:
    assert decode_updates(message_type, fields) == (expected,)


def test_change_message_without_data_has_no_updates() -> None:
    assert decode_updates(0x0302, []) == ()


def test_unknown_message_type_is_preserved() -> None:
    frame = decode_frame(VERSION_INFO)
    unknown = Frame(version=3, speaker=1, group=15, message_type=0x7777,
                    increment=None, fields=frame.fields, length=frame.length)
    message: Message = decode_message(unknown)
    assert message == UnknownMessage(unknown)


def test_changes_merge_into_settings() -> None:
    settings = snapshot_settings()
    settings.apply(CountdownUpdate(OutputId.C2, True, 3600, 3000))
    assert settings.outputs[OutputId.C2].countdown_remaining == 3000
    settings.apply(OutputSwitchUpdate(OutputId.C2, False))
    control = settings.outputs[OutputId.C2]
    assert not control.countdown_enabled and control.countdown_total == 0

    settings.apply(ChargingModeUpdate(2, priority=0b0011))
    assert settings.charging_mode == 2
    assert settings.priority_bitmap == 0b0011
    settings.apply(ChargingModeUpdate(1))
    assert settings.priority_bitmap == 0
    assert settings.outputs[OutputId.C1].priority == 0

    settings.apply(ValueUpdate(SettingField.ERROR_CODE, 0b0100))
    assert settings.has_fault
    assert settings.output_fault(OutputId.C2)
    assert not settings.output_fault(OutputId.C1)
    settings.apply(ValueUpdate(SettingField.ERROR_CODE, 1))
    assert settings.over_temperature
    assert not settings.output_fault(OutputId.C2)


def test_clock_change_keeps_known_theme_url() -> None:
    settings = DeviceSettings()
    with_url = ClockState(True, False, 3, theme_id=7, theme_url='https://x/y')
    settings.apply(ClockUpdate(with_url, False))
    settings.apply(ClockUpdate(ClockState(False, False, 3, theme_id=7)))
    clock = settings.clock
    assert clock is not None
    assert not clock.is_on and clock.theme_url == 'https://x/y'
    assert settings.image_time_display is False


def test_theme_download_sets_picture_theme() -> None:
    settings = DeviceSettings()
    settings.apply(ThemeDownloadUpdate(42, 0, 5))
    assert settings.clock is None
    settings.apply(ThemeDownloadUpdate(42, 1, 5))
    assert settings.clock == ClockState(True, False, 5, theme_id=42)


def test_single_output_priority_update() -> None:
    settings = snapshot_settings()
    settings.apply(PriorityUpdate(output=OutputId.C3, preferred=True))
    assert settings.priority_bitmap == 0b0100


def test_snapshot_keeps_theme_status_details() -> None:
    previous = DeviceSettings()
    clock = ClockState(True, False, 3, theme_id=7, theme_url='https://x/y',
                       showing_holiday=True)
    previous.apply(ClockUpdate(clock, True))
    fresh = snapshot_settings()
    fresh.apply(ClockUpdate(ClockState(True, False, 3, theme_id=7)))
    fresh.keep_theme_details(previous)
    kept = fresh.clock
    assert kept is not None
    assert kept.theme_url == 'https://x/y' and kept.showing_holiday is True
    assert fresh.image_time_display is True

    changed = snapshot_settings()
    changed.keep_theme_details(previous)
    assert changed.clock is not None and changed.clock.theme_url is None


@pytest.mark.parametrize(('field', 'value', 'attribute', 'expected'), [
    (SettingField.HOUR_FORMAT, 0, 'hour_format', 0),
    (SettingField.MAX_COMPATIBILITY, 1, 'max_compatibility', True),
    (SettingField.DEVICE_IDENTIFICATION, 0, 'device_identification', False),
    (SettingField.UFCS, 1, 'ufcs', True),
])
def test_acknowledged_write_values(
        field: SettingField, value: int, attribute: str,
        expected: object) -> None:
    settings = DeviceSettings()
    settings.apply(ValueUpdate(field, value))
    assert getattr(settings, attribute) == expected
