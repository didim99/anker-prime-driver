"""A2345 command builder tests against the official app's frames."""

import pytest

from ankerprime.model import (
    ChargingMode, ClockSchedule, ClockTheme, InvalidParameterError,
    OutputId, Schedule, ScheduleSlot, ScreenTimeout, Weekday)
from ankerprime.protocol.a2345 import commands
from ankerprime.protocol.a2345.frame import checksum, decode_frame
from ankerprime.protocol.a2345.settings import (
    CustomProfile, PortProtocol, WriteAcknowledgement)
from tests.protocol.a2345.fixtures import (
    REALTIME_TRIGGER_REQUEST, REQUEST_EPOCH,
    STAMP_A, STAMP_B, STATUS_SNAPSHOT_REQUEST)


def test_read_requests_match_fixed_vectors() -> None:
    snapshot = commands.status_snapshot().encode(REQUEST_EPOCH)
    assert snapshot == STATUS_SNAPSHOT_REQUEST
    trigger = commands.realtime_trigger().encode(REQUEST_EPOCH)
    assert trigger == REALTIME_TRIGGER_REQUEST


@pytest.mark.parametrize(('command', 'expected'), [
    (commands.set_screen_timeout(ScreenTimeout.THIRTY_SECONDS),
     'ff09180003000f0203a10122a2020101fe0503e3ccbd6ac1'),
    (commands.set_brightness(50),
     'ff09180003000f0204a10122a2020132fe0503e3ccbd6af5'),
    (commands.theme_status(), 'ff09140003000f0202a10122fe0503e3ccbd6a6c'),
    (commands.set_clock_schedule(ClockSchedule(8, 0, 23, 0)),
     'ff091c0003000f0213a10122a20604080017007ffe0503e3ccbd6ab5'),
])
def test_settings_match_official_encoder(command: commands.Command,
                                         expected: str) -> None:
    assert command.encode(STAMP_A).hex() == expected


def test_output_switch_matches_captured_frame() -> None:
    c1_on = commands.set_output(OutputId.C1, True).encode(STAMP_B)
    assert c1_on.hex() == ('ff091c0003000f0207a10122a2020100a3020101'
                           'fe05030078e7686e')
    usb_a_off = commands.set_output(OutputId.USB_A, False).encode(STAMP_B)
    assert usb_a_off[12:20] == bytes((0xA2, 2, 1, 4, 0xA3, 2, 1, 0))


def test_schedule_carries_four_byte_value() -> None:
    schedule = Schedule(True, 7, 30, Weekday(0x1F))
    command = commands.set_schedule(OutputId.C2, ScheduleSlot.POWER_ON,
                                    schedule)
    assert command.encode(STAMP_B).hex() == (
        'ff09230003000f0208a10122a2020101a3020101a4050301071e1f'
        'fe05030078e768fa')


def test_countdown_carries_switch_and_seconds() -> None:
    frame = commands.set_countdown(OutputId.C1, True, 900).encode(STAMP_B)
    assert frame[12:24].hex() == 'a2020100a306040184030000'
    with pytest.raises(InvalidParameterError):
        commands.set_countdown(OutputId.C1, True, 90_000)


def test_cancelling_countdown_echoes_any_total() -> None:
    command = commands.set_countdown(OutputId.C3, False, 100_000)
    frame = decode_frame(command.encode(STAMP_A))
    assert frame.fields[2].raw == bytes((4, 0, 0xA0, 0x86, 1, 0))


def test_custom_mode_matches_captured_profile_and_protocols() -> None:
    profile = CustomProfile(1, False, (15, 22, 25, 20, 15))
    ufcs = PortProtocol.UFCS
    mixed = PortProtocol.SCP | PortProtocol.UFCS | PortProtocol.PPS_11V
    command = commands.set_custom_charging_mode(profile,
                                                [ufcs, mixed, mixed, ufcs])
    frame = decode_frame(command.encode(STAMP_B))
    assert frame.message_type == 0x0206
    assert [field.tag for field in frame.fields] == [
        0xA1, 0xA2, 0xA3, 0xA4, 0xFE]
    assert frame.fields[1].raw == b'\x01\x05'
    assert frame.fields[2].raw.hex() == '0401000f1619140f'
    assert frame.fields[3].raw.hex() == '040200000b00000b0000020000'


@pytest.mark.parametrize('watts', [
    (10, 0, 0, 0, 0),       # C port inside the 1–14 W gap
    (140, 100, 0, 0, 20),   # USB-A takes only 0, 15 or 24
    (140, 100, 100, 0, 0),  # over 250 W in total
])
def test_custom_mode_rejects_values_the_app_never_sends(
        watts: tuple[int, ...]) -> None:
    with pytest.raises(InvalidParameterError):
        commands.set_custom_charging_mode(CustomProfile(1, False, watts))


def test_inverted_and_plain_switches() -> None:
    assert commands.set_max_compatibility(True).encode(STAMP_A)[15] == 0
    assert commands.set_max_compatibility(False).encode(STAMP_A)[15] == 1
    assert commands.set_device_identification(True).encode(STAMP_A)[15] == 1
    assert commands.exit_custom_charging_mode().encode(STAMP_A)[15] == 0
    assert commands.set_max_compatibility(True).ack_type == 0x0A14
    assert commands.status_snapshot().ack_type is None
    assert commands.realtime_trigger().ack_type == 0x0A0B


def test_read_and_write_kinds() -> None:
    assert not commands.status_snapshot().is_write
    assert not commands.realtime_trigger().is_write
    assert commands.set_output(OutputId.C1, True).is_write


def test_preferred_outputs_allow_at_most_two_c_ports() -> None:
    command = commands.set_preferred_outputs([OutputId.C1, OutputId.C3])
    assert command.fields[0xA2] == bytes((0b0101,))
    with pytest.raises(InvalidParameterError):
        commands.set_preferred_outputs([OutputId.C1, OutputId.C2, OutputId.C3])
    with pytest.raises(InvalidParameterError):
        commands.set_preferred_outputs([OutputId.USB_A])


@pytest.mark.parametrize('percent', [15, 22, 105])
def test_brightness_range(percent: int) -> None:
    with pytest.raises(InvalidParameterError):
        commands.set_brightness(percent)


def test_unsupported_values_are_rejected() -> None:
    with pytest.raises(InvalidParameterError):
        commands.set_screen_timeout(ScreenTimeout.TWELVE_HOURS)
    with pytest.raises(InvalidParameterError):
        commands.set_charging_mode(ChargingMode.CUSTOM)
    with pytest.raises(InvalidParameterError):
        commands.set_charging_mode(ChargingMode.STANDARD)


def test_builtin_theme_sends_empty_id_and_asset_path() -> None:
    url = commands.builtin_theme_url(ClockTheme.STYLE_2)
    command = commands.set_clock_theme(True, ClockTheme.STYLE_2, url=url)
    frame = decode_frame(command.encode(STAMP_A))
    assert [field.tag for field in frame.fields] == [
        0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xFE]
    assert frame.fields[1].raw == b'\x01\x81'
    assert frame.fields[2].raw == b'\x04'
    assert frame.fields[4].raw == b'\x03\xff\xff\xff\xff'
    assert frame.fields[5].raw[1:] == b'assets/img/a2345/icl_a2345_theme2.png'


def test_custom_picture_uses_long_a7() -> None:
    url = 'https://example.com/' + 'x' * 180
    command = commands.set_clock_theme(True, ClockTheme.CUSTOM, theme_id=77,
                                       image_crc=0x12345678, custom_url=url)
    frame = command.encode(STAMP_A)
    a7 = frame.index(0xA7)
    assert int.from_bytes(frame[a7 + 1:a7 + 3], 'little') == len(url) + 1
    assert frame[a7 + 3] == 0x04
    assert checksum(frame[:-1]) == frame[-1]


def test_picture_theme_needs_id() -> None:
    with pytest.raises(InvalidParameterError):
        commands.set_clock_theme(True, ClockTheme.PICTURE)


def test_ack_matching_uses_echoed_output_and_slot() -> None:
    command = commands.set_schedule(OutputId.C2, ScheduleSlot.POWER_ON,
                                    Schedule(True, 7, 0))
    assert command.matches(WriteAcknowledgement(0x0A08, 0))
    assert command.matches(WriteAcknowledgement(
        0x0A08, 0, output=OutputId.C2, slot=ScheduleSlot.POWER_ON))
    assert not command.matches(WriteAcknowledgement(
        0x0A08, 0, output=OutputId.C3))
    assert not command.matches(WriteAcknowledgement(
        0x0A08, 0, slot=ScheduleSlot.POWER_OFF))
    assert not command.matches(WriteAcknowledgement(0x0A07, 0))
