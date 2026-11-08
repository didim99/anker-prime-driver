"""Builders for every request the A2345 accepts.

The set is closed: each builder matches one setter of the official app and
validates its arguments the same way; there is no raw-bytes request.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar

from ankerprime.model.custom import ChargingProfile, CustomProtocol
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, HourFormat, KnobDirection,
    OutputId, ScheduleSlot, ScreenTimeout)
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.state import ClockSchedule, Schedule
from ankerprime.protocol.a2345.codes import (
    CHARGING_MODE_CODES, CLOCK_THEME_CODES, HOUR_FORMAT_CODES,
    KNOB_DIRECTION_CODES, SCHEDULE_SLOT_CODES, GROUPS,
    SCREEN_TIMEOUT_CODES, USB_C_GROUPS, group_code)
from ankerprime.protocol.a2345.frame import (
    CLOUD_SESSION, MessageType, encode_request)
from ankerprime.protocol.a2345.settings import (
    ACK_PREFIX, CUSTOM_CHARGING_MODE_CODE, CustomProfile, PortProtocol)
from ankerprime.protocol.a2345.updates import (
    ClockUpdate, SettingsChange, WriteAcknowledgement)
from ankerprime.protocol.codes import encode_value
from ankerprime.utils import u32le


MAX_COUNTDOWN = 86_400
BRIGHTNESS_MIN = 20
BRIGHTNESS_MAX = 100
BRIGHTNESS_STEP = 5
MAX_PREFERRED_OUTPUTS = 2
DEFAULT_TEXT_COLOR = 0xFFFFFFFF
MAX_URL_LENGTH = 254
MAX_CUSTOM_URL_LENGTH = 1024

CUSTOM_PROFILE_NUMBERS = range(1, 5)
CUSTOM_TOTAL_LIMIT = 250
CUSTOM_USB_A_CHOICES = (0, 15, 24)
CUSTOM_C_RANGE = range(15, 141)


@dataclass(init=False)
class Command:
    """One encodable request and how to recognise its acknowledgement."""

    READ_TYPES: ClassVar[frozenset[int]] = frozenset({
        MessageType.STATUS_SNAPSHOT_REQUEST, MessageType.THEME_STATUS_REQUEST,
        MessageType.REALTIME_TRIGGER})
    # Reads answered by their data message instead of an ack.
    DATA_ANSWERED_TYPES: ClassVar[frozenset[int]] = frozenset({
        MessageType.STATUS_SNAPSHOT_REQUEST, MessageType.THEME_STATUS_REQUEST})
    # Writes the charger answers with a theme status instead of an ack (seen
    # on hardware, firmware 2.1.1.6); the status shows whether it applied.
    THEME_ANSWERED_TYPES: ClassVar[frozenset[int]] = frozenset({
        MessageType.IMAGE_TIME_DISPLAY})

    _message_type: int
    _fields: Mapping[int, bytes]
    _allow_long_fields: bool
    _output: OutputId | None
    _slot: ScheduleSlot | None

    def __init__(self, message_type: int, fields: Mapping[int, bytes], *,
                 allow_long_fields: bool = False,
                 output: OutputId | None = None,
                 slot: ScheduleSlot | None = None):
        self._message_type = message_type
        self._fields = MappingProxyType(dict(fields))
        self._allow_long_fields = allow_long_fields
        self._output = output
        self._slot = slot

    @property
    def message_type(self) -> int:
        return self._message_type

    @property
    def fields(self) -> Mapping[int, bytes]:
        return self._fields

    @property
    def output(self) -> OutputId | None:
        """Output the command targets, echoed by its acknowledgement."""

        return self._output

    @property
    def slot(self) -> ScheduleSlot | None:
        return self._slot

    @property
    def is_write(self) -> bool:
        return self._message_type not in self.READ_TYPES

    @property
    def theme_answered(self) -> bool:
        """Whether a theme status, not an ack, confirms this write."""

        return self._message_type in self.THEME_ANSWERED_TYPES

    @property
    def ack_type(self) -> int | None:
        """Type of the ``0x0ANN`` answer; None when a data message answers."""

        if self._message_type in (self.DATA_ANSWERED_TYPES
                                  | self.THEME_ANSWERED_TYPES):
            return None
        return ACK_PREFIX | (self._message_type & 0xFF)

    def encode(self, timestamp: int, session: int = CLOUD_SESSION) -> bytes:
        return encode_request(self._message_type, self._fields,
                              timestamp=timestamp, session=session,
                              allow_long_fields=self._allow_long_fields)

    def confirmed_by(self, change: SettingsChange) -> bool:
        """Check that a theme status shows this write applied."""

        if (self._message_type not in self.THEME_ANSWERED_TYPES
                or change.message_type != MessageType.THEME_STATUS):
            return False
        enabled = bool(self._fields[0xA2][0])
        return any(isinstance(update, ClockUpdate)
                   and update.image_time_display is enabled
                   for update in change.updates)

    def matches(self, ack: WriteAcknowledgement) -> bool:
        """Check that the ack answers this command; unechoed parts match any."""

        if ack.message_type != self.ack_type:
            return False
        return (_echo_matches(ack.output, self._output)
                and _echo_matches(ack.slot, self._slot))


def status_snapshot() -> Command:
    """Request a full ``0x0A00`` snapshot."""

    return Command(MessageType.STATUS_SNAPSHOT_REQUEST, {})


def theme_status() -> Command:
    """Request the ``0x0A02`` clock theme state the snapshot leaves out."""

    return Command(MessageType.THEME_STATUS_REQUEST, {})


def realtime_trigger() -> Command:
    """Start a 10-second window of ``0x0303`` realtime messages."""

    return Command(MessageType.REALTIME_TRIGGER, {})


def set_output(output: OutputId, on: bool) -> Command:
    fields = {0xA2: _u8(group_code(output)), 0xA3: _u8(on)}
    return Command(MessageType.OUTPUT_SWITCH, fields, output=output)


def set_schedule(output: OutputId, slot: ScheduleSlot,
                 schedule: Schedule) -> Command:
    """Set one daily slot; weekday bit 0 = Monday, no bits = once."""

    value = bytes((schedule.enabled, schedule.hour, schedule.minute,
                   int(schedule.weekdays) & 0x7F))
    fields = {0xA2: _u8(group_code(output)),
              0xA3: _u8(SCHEDULE_SLOT_CODES[slot]), 0xA4: value}
    return Command(MessageType.SCHEDULE, fields, output=output, slot=slot)


def set_countdown(output: OutputId, enabled: bool, seconds: int) -> Command:
    """Start or cancel the auto-off countdown.

    Cancelling sends the reported total back unchanged, so its range is not
    checked.
    """

    if enabled and not 1 <= seconds <= MAX_COUNTDOWN:
        raise InvalidParameterError(f'countdown out of range: {seconds}')
    value = _u8(enabled) + u32le(seconds)
    fields = {0xA2: _u8(group_code(output)), 0xA3: value}
    return Command(MessageType.COUNTDOWN, fields, output=output)


def set_charging_mode(mode: ChargingMode) -> Command:
    """Select a preset mode; custom mode needs ``set_custom_charging_mode``."""

    if mode == ChargingMode.CUSTOM:
        raise InvalidParameterError('custom mode needs a profile')
    code = encode_value(CHARGING_MODE_CODES, mode)
    return Command(MessageType.CHARGING_MODE, {0xA2: _u8(code)})


def set_custom_charging_mode(profile: CustomProfile,
                             protocols: Sequence[PortProtocol] | None = None
                             ) -> Command:
    """Activate custom mode with per-port limits and optional C1–C4 protocols.

    Limits: C ports 0 or 15–140 W, the USB-A pair 0, 15 or 24 W, at most
    250 W in total.
    """

    _check_custom_profile(profile)
    fields = {0xA2: _u8(CUSTOM_CHARGING_MODE_CODE),
              0xA3: bytes((profile.number, profile.auto_exit, *profile.watts))}
    if protocols is not None:
        if len(protocols) != len(USB_C_GROUPS):
            raise InvalidParameterError('protocols needed for C1–C4')
        fields[0xA4] = b''.join(bytes((flags & 0x7F, 0, 0))
                                for flags in protocols)
    return Command(MessageType.CHARGING_MODE, fields)


def apply_custom_profile(profile: ChargingProfile, *, mainland: bool,
                         with_protocols: bool) -> Command:
    """Activate a model profile; protocols only when ``with_protocols``.

    Mainland firmware reads the SCP bit as Huawei and adds Xiaomi.
    """

    watts = [profile.watts(output) for output in GROUPS]
    custom = CustomProfile(profile.number, profile.auto_exit, watts)
    protocols = None
    if with_protocols:
        protocols = [port_protocols(profile, output, mainland)
                     for output in USB_C_GROUPS]
    return set_custom_charging_mode(custom, protocols)


def port_protocols(profile: ChargingProfile, output: OutputId,
                   mainland: bool) -> PortProtocol:
    """Protocol bits of one C port as the charger expects them."""

    limit = profile.port(output)
    flags = PortProtocol(0)
    if limit is None:
        return flags
    scp = CustomProtocol.HUAWEI if mainland else CustomProtocol.SCP
    bits = {scp: PortProtocol.SCP,
            CustomProtocol.UFCS: PortProtocol.UFCS,
            CustomProtocol.PD_12V: PortProtocol.PD_12V,
            CustomProtocol.PPS_11V: PortProtocol.PPS_11V,
            CustomProtocol.PPS_16V: PortProtocol.PPS_16V,
            CustomProtocol.PPS_21V: PortProtocol.PPS_21V}
    if mainland:
        bits[CustomProtocol.XIAOMI] = PortProtocol.XIAOMI
    for protocol, bit in bits.items():
        if limit.allows(protocol):
            flags |= bit
    return flags


def exit_custom_charging_mode() -> Command:
    return Command(MessageType.EXIT_CUSTOM_MODE, {0xA2: _u8(0)})


def set_preferred_outputs(outputs: Collection[OutputId]) -> Command:
    """Choose up to two preferred C ports of the port-priority mode."""

    if len(outputs) > MAX_PREFERRED_OUTPUTS:
        raise InvalidParameterError('at most two preferred outputs')
    bitmap = 0
    for output in outputs:
        if output not in USB_C_GROUPS:
            raise InvalidParameterError(f'{output} cannot be preferred')
        bitmap |= 1 << USB_C_GROUPS.index(output)
    return Command(MessageType.PORT_PRIORITY, {0xA2: _u8(bitmap)})


def set_screen_timeout(timeout: ScreenTimeout) -> Command:
    code = encode_value(SCREEN_TIMEOUT_CODES, timeout)
    return Command(MessageType.SCREEN_TIMEOUT, {0xA2: _u8(code)})


def set_brightness(percent: int) -> Command:
    """Set the display brightness: 20–100 % in steps of 5."""

    if (not BRIGHTNESS_MIN <= percent <= BRIGHTNESS_MAX
            or percent % BRIGHTNESS_STEP):
        raise InvalidParameterError(f'brightness out of range: {percent}')
    return Command(MessageType.BRIGHTNESS, {0xA2: _u8(percent)})


def set_knob_direction(direction: KnobDirection) -> Command:
    code = encode_value(KNOB_DIRECTION_CODES, direction)
    return Command(MessageType.KNOB_DIRECTION, {0xA2: _u8(code)})


def set_hour_format(hour_format: HourFormat) -> Command:
    code = encode_value(HOUR_FORMAT_CODES, hour_format)
    return Command(MessageType.HOUR_FORMAT, {0xA2: _u8(code)})


def set_clock_schedule(schedule: ClockSchedule) -> Command:
    value = bytes((schedule.start_hour, schedule.start_minute,
                   schedule.end_hour, schedule.end_minute,
                   int(schedule.weekdays) & 0x7F))
    return Command(MessageType.CLOCK_SCHEDULE, {0xA2: value})


def set_holiday_theme(enabled: bool) -> Command:
    return Command(MessageType.HOLIDAY_THEME, {0xA2: _u8(enabled)})


def set_clock_theme(clock_on: bool, theme: ClockTheme, *,
                    theme_id: int | None = None, image_crc: int = 0,
                    text_color: int = DEFAULT_TEXT_COLOR, url: str = '',
                    custom_url: str | None = None) -> Command:
    """Switch the clock screensaver and pick its theme.

    There is no separate switch command: switching resends the current theme.
    Built-in styles send the official app's asset path as ``url`` (see
    ``builtin_theme_url``); a custom picture link goes in its own long field.
    """

    code = encode_value(CLOCK_THEME_CODES, theme)
    fields = {0xA2: _u8((0x80 if clock_on else 0) | (code & 0x7F))}
    if theme in (ClockTheme.PICTURE, ClockTheme.CUSTOM):
        if theme_id is None:
            raise InvalidParameterError('picture themes need a theme id')
        fields[0xA3] = u32le(theme_id)
    else:
        fields[0xA3] = b''
    fields[0xA4] = u32le(image_crc)
    fields[0xA5] = u32le(text_color)
    if custom_url:
        encoded = custom_url.encode('utf-8')
        if len(encoded) > MAX_CUSTOM_URL_LENGTH:
            raise InvalidParameterError('custom picture URL too long')
        fields[0xA7] = encoded
        return Command(MessageType.CLOCK_THEME, fields, allow_long_fields=True)
    encoded = url.encode('utf-8')
    if len(encoded) > MAX_URL_LENGTH:
        raise InvalidParameterError('theme URL too long')
    fields[0xA6] = encoded
    return Command(MessageType.CLOCK_THEME, fields)


def builtin_theme_url(theme: ClockTheme) -> str:
    """Asset path the official app sends for a built-in style."""

    number = CLOCK_THEME_CODES[theme] + 1
    return f'assets/img/a2345/icl_a2345_theme{number}.png'


def set_image_time_display(enabled: bool) -> Command:
    """Show the time over a custom clock picture."""

    return Command(MessageType.IMAGE_TIME_DISPLAY, {0xA2: _u8(enabled)})


def set_max_compatibility(enabled: bool) -> Command:
    """Toggle maximum compatibility; the wire value is inverted."""

    return Command(MessageType.MAX_COMPATIBILITY, {0xA2: _u8(not enabled)})


def set_device_identification(enabled: bool) -> Command:
    return Command(MessageType.DEVICE_IDENTIFICATION, {0xA2: _u8(enabled)})


def set_ufcs(enabled: bool) -> Command:
    """Toggle UFCS on mainland firmware without protocol management."""

    return Command(MessageType.UFCS, {0xA2: _u8(enabled)})


def _check_custom_profile(profile: CustomProfile) -> None:
    watts = profile.watts
    valid = (profile.number in CUSTOM_PROFILE_NUMBERS and len(watts) == 5
             and sum(watts) <= CUSTOM_TOTAL_LIMIT
             and watts[4] in CUSTOM_USB_A_CHOICES
             and all(value == 0 or value in CUSTOM_C_RANGE
                     for value in watts[:4]))
    if not valid:
        raise InvalidParameterError(f'invalid custom profile: {profile}')


def _echo_matches(echoed: object | None, expected: object | None) -> bool:
    return echoed is None or expected is None or echoed == expected


def _u8(value: int) -> bytes:
    return bytes((int(value),))
