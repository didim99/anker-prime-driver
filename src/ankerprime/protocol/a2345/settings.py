"""A2345 settings: snapshot fields, change messages and write acknowledgements.

Values are read the way the official app does: the first field with a tag
wins and its leading value-type byte is dropped whatever it is.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from types import MappingProxyType

from ankerprime.model.enums import OutputId, ScheduleSlot
from ankerprime.protocol.a2345.codes import (
    GROUPS, SCHEDULE_SLOT_CODES, USB_C_GROUPS, output_of_group)
from ankerprime.protocol.a2345.frame import Frame, Message
from ankerprime.protocol.codes import decode_value


MSG_STATUS_SNAPSHOT = 0x0A00
MSG_FIRMWARE_VERSION_CHANGE = 0x0301
MSG_OUTPUT_CHANGE = 0x0302
MSG_ERROR_CODE_CHANGE = 0x0304
MSG_COUNTDOWN_CHANGE = 0x0307
MSG_SCHEDULE_CHANGE = 0x0308
MSG_SCREEN_TIMEOUT_CHANGE = 0x0309
MSG_BRIGHTNESS_CHANGE = 0x030A
MSG_CLOCK_CHANGE = 0x030B
MSG_CHARGING_MODE_CHANGE = 0x030D
MSG_PRIORITY_CHANGE = 0x030F
MSG_KNOB_DIRECTION_CHANGE = 0x0310
MSG_THEME_DOWNLOAD_CHANGE = 0x0311
MSG_THEME_STATUS = 0x0A02

ACK_PREFIX = 0x0A00
ACK_OUTPUT_SWITCH = 0x0A07
ACK_SCHEDULE = 0x0A08
ACK_COUNTDOWN = 0x0A09

TAG_FIRMWARE = 0xA2
TAG_ERROR_CODE = 0xA3
TAG_FIRST_OUTPUT_CONTROL = 0xAA
TAG_CLOCK = 0xAF
TAG_SCREEN_TIMEOUT = 0xB0
TAG_CHARGING_MODE = 0xB1
TAG_UPDATING_FIRMWARE = 0xB2
TAG_BRIGHTNESS = 0xB3
TAG_KNOB_DIRECTION = 0xB4
TAG_HOUR_FORMAT = 0xB5
TAG_PROTOCOL_VERSION = 0xB6
TAG_CAPABILITIES = 0xB7
TAG_CUSTOM_PROFILE = 0xB8
TAG_CLOCK_SCHEDULE = 0xB9
TAG_PORT_PROTOCOLS = 0xBA
TAG_MAX_COMPATIBILITY = 0xBB
TAG_UFCS = 0xBC
TAG_USB_IDENTITY = 0xBD
TAG_BRAND_IDENTITY = 0xBE
TAG_DEVICE_IDENTIFICATION = 0xBF

CUSTOM_CHARGING_MODE_CODE = 5
PORT_PRIORITY_MODE_CODE = 2
OVER_TEMPERATURE_CODE = 1
PRIORITY_OFF = 0
PRIORITY_NORMAL = 1
PRIORITY_PREFERRED = 2
SCHEDULE_UNSET = 0xFF
OUTPUT_CONTROL_LENGTH = 19
THEME_PICTURE_CODES = (3, 5)


class SettingField(StrEnum):
    """Single-value setting carried by a ``ValueUpdate``.

    The last four have no change message; they are only applied after an
    acknowledged write, until the next snapshot confirms them.
    """

    BRIGHTNESS = 'brightness'
    ERROR_CODE = 'error_code'
    KNOB_DIRECTION = 'knob_direction'
    SCREEN_TIMEOUT = 'screen_timeout'
    HOUR_FORMAT = 'hour_format'
    MAX_COMPATIBILITY = 'max_compatibility'
    DEVICE_IDENTIFICATION = 'device_identification'
    UFCS = 'ufcs'


class Capabilities(IntFlag):
    """Feature bits the firmware reports in the snapshot (B7)."""

    CUSTOM_CHARGING_MODE = 1 << 0
    CLOCK_SCHEDULE = 1 << 1
    BLUETOOTH_FIRMWARE_UPDATE = 1 << 2
    DUAL_LOG_CHANNEL = 1 << 3
    PORT_PROTOCOLS = 1 << 4
    MAX_COMPATIBILITY = 1 << 5
    CUSTOM_IMAGE_TIME_DISPLAY = 1 << 6
    PICTURE_THEMES_OVER_BLUETOOTH = 1 << 7
    UFCS_SWITCH = 1 << 8
    DEVICE_IDENTIFICATION = 1 << 9
    BRAND_MODEL_CODES = 1 << 10
    PROTOCOL_MANAGEMENT = 1 << 11


class PortProtocol(IntFlag):
    """Fast-charge protocols allowed on a USB-C port in custom mode.

    Mainland firmware reuses the SCP bit for Huawei.
    """

    SCP = 1 << 0
    UFCS = 1 << 1
    PD_12V = 1 << 2
    PPS_11V = 1 << 3
    PPS_16V = 1 << 4
    PPS_21V = 1 << 5
    XIAOMI = 1 << 6


@dataclass(init=False)
class PortSchedule:
    """One daily slot as reported; ``0xFF`` in the switch byte means unset."""

    _switch: int
    _hour: int
    _minute: int
    _weekdays: int

    def __init__(self, switch: int, hour: int, minute: int, weekdays: int):
        self._switch = switch
        self._hour = hour
        self._minute = minute
        self._weekdays = weekdays

    @property
    def switch(self) -> int:
        return self._switch

    @property
    def hour(self) -> int:
        return self._hour

    @property
    def minute(self) -> int:
        return self._minute

    @property
    def weekdays(self) -> int:
        """Raw mask, bit 0 = Monday; unset slots may carry extra bits."""

        return self._weekdays

    @property
    def enabled(self) -> bool:
        return self._switch == 1

    @property
    def unset(self) -> bool:
        return self._switch == SCHEDULE_UNSET

    @property
    def repeat_days(self) -> int:
        """Weekday mask without the bits of unset slots."""

        return 0 if self.unset else self._weekdays & 0x7F


@dataclass(init=False)
class OutputControl:
    """Switch, schedules, countdown and priority of one output."""

    _is_on: bool
    _power_on: PortSchedule
    _power_off: PortSchedule
    _countdown_enabled: bool
    _countdown_total: int
    _countdown_remaining: int
    _priority: int

    def __init__(self, *, is_on: bool, power_on: PortSchedule,
                 power_off: PortSchedule, countdown_enabled: bool,
                 countdown_total: int, countdown_remaining: int,
                 priority: int):
        self._is_on = is_on
        self._power_on = power_on
        self._power_off = power_off
        self._countdown_enabled = countdown_enabled
        self._countdown_total = countdown_total
        self._countdown_remaining = countdown_remaining
        self._priority = priority

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def power_on(self) -> PortSchedule:
        return self._power_on

    @property
    def power_off(self) -> PortSchedule:
        return self._power_off

    @property
    def countdown_enabled(self) -> bool:
        return self._countdown_enabled

    @property
    def countdown_total(self) -> int:
        return self._countdown_total

    @property
    def countdown_remaining(self) -> int:
        return self._countdown_remaining

    @property
    def priority(self) -> int:
        """0 outside port-priority mode, 1 normal, 2 preferred."""

        return self._priority

    def schedule(self, slot: ScheduleSlot) -> PortSchedule:
        if slot == ScheduleSlot.POWER_ON:
            return self._power_on
        return self._power_off

    def set_switch(self, is_on: bool) -> None:
        """Set the switch; switching off also cancels the countdown."""

        self._is_on = is_on
        if not is_on:
            self.set_countdown(False, 0, 0)

    def set_countdown(self, enabled: bool, total: int, remaining: int) -> None:
        self._countdown_enabled = enabled
        self._countdown_total = total
        self._countdown_remaining = remaining

    def set_schedule(self, slot: ScheduleSlot, schedule: PortSchedule) -> None:
        if slot == ScheduleSlot.POWER_ON:
            self._power_on = schedule
        else:
            self._power_off = schedule

    def set_priority(self, priority: int) -> None:
        self._priority = priority


@dataclass(init=False)
class ClockState:
    """Clock screensaver state.

    Theme codes: 0–2 built-in styles, 3 cloud picture, 5 custom picture.
    """

    _is_on: bool
    _holiday_enabled: bool
    _theme_code: int
    _theme_id: int | None
    _theme_url: str | None
    _showing_holiday: bool | None

    def __init__(self, is_on: bool, holiday_enabled: bool, theme_code: int, *,
                 theme_id: int | None = None, theme_url: str | None = None,
                 showing_holiday: bool | None = None):
        self._is_on = is_on
        self._holiday_enabled = holiday_enabled
        self._theme_code = theme_code
        self._theme_id = theme_id
        self._theme_url = theme_url
        self._showing_holiday = showing_holiday

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def holiday_enabled(self) -> bool:
        return self._holiday_enabled

    @property
    def theme_code(self) -> int:
        return self._theme_code

    @property
    def theme_id(self) -> int | None:
        return self._theme_id

    @property
    def theme_url(self) -> str | None:
        return self._theme_url

    @property
    def showing_holiday(self) -> bool | None:
        """A holiday theme is on screen right now."""

        return self._showing_holiday

    def set_theme(self, theme_code: int, theme_id: int | None) -> None:
        self._theme_code = theme_code
        self._theme_id = theme_id

    def set_theme_url(self, theme_url: str | None) -> None:
        self._theme_url = theme_url

    def set_showing_holiday(self, showing_holiday: bool | None) -> None:
        self._showing_holiday = showing_holiday

    @classmethod
    def from_flags(cls, flags: int, *, theme_id: int | None = None,
                   theme_url: str | None = None,
                   showing_holiday: bool | None = None) -> ClockState:
        """Build from the flags byte: bit 7 on, bit 6 holiday, rest theme.

        The theme id is kept for picture themes only.
        """

        theme_code = flags & 0x3F
        if theme_code not in THEME_PICTURE_CODES:
            theme_id = None
        return cls(bool(flags & 0x80), bool(flags & 0x40), theme_code,
                   theme_id=theme_id, theme_url=theme_url,
                   showing_holiday=showing_holiday)


@dataclass(init=False)
class ClockWindow:
    """Time window of the clock screensaver; weekday bit 0 = Monday."""

    _start_hour: int
    _start_minute: int
    _end_hour: int
    _end_minute: int
    _weekdays: int

    def __init__(self, start_hour: int, start_minute: int, end_hour: int,
                 end_minute: int, weekdays: int):
        self._start_hour = start_hour
        self._start_minute = start_minute
        self._end_hour = end_hour
        self._end_minute = end_minute
        self._weekdays = weekdays

    @property
    def start_hour(self) -> int:
        return self._start_hour

    @property
    def start_minute(self) -> int:
        return self._start_minute

    @property
    def end_hour(self) -> int:
        return self._end_hour

    @property
    def end_minute(self) -> int:
        return self._end_minute

    @property
    def weekdays(self) -> int:
        return self._weekdays


@dataclass(init=False)
class CustomProfile:
    """Custom charging mode limits in watts for C1–C4 and the USB-A pair."""

    _number: int
    _auto_exit: bool
    _watts: tuple[int, ...]

    def __init__(self, number: int, auto_exit: bool, watts: Iterable[int]):
        self._number = number
        self._auto_exit = auto_exit
        self._watts = tuple(watts)

    @property
    def number(self) -> int:
        return self._number

    @property
    def auto_exit(self) -> bool:
        return self._auto_exit

    @property
    def watts(self) -> tuple[int, ...]:
        return self._watts


@dataclass(init=False)
class IdentitySlot:
    """USB vendor/product ids (BD) or brand/model codes (BE) of a C port."""

    _output: OutputId
    _first: int
    _second: int

    def __init__(self, output: OutputId, first: int, second: int):
        self._output = output
        self._first = first
        self._second = second

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def first(self) -> int:
        return self._first

    @property
    def second(self) -> int:
        return self._second

    @property
    def empty(self) -> bool:
        """Nothing identified: FFFA:FFFB, all zeros or all ones."""

        pair = (self._first, self._second)
        return pair in ((0xFFFA, 0xFFFB), (0, 0), (0xFFFF, 0xFFFF))


class SettingsUpdate:
    """One change message reduced to the setting it touches."""


@dataclass(init=False)
class OutputSwitchUpdate(SettingsUpdate):
    _output: OutputId
    _is_on: bool

    def __init__(self, output: OutputId, is_on: bool):
        self._output = output
        self._is_on = is_on

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def is_on(self) -> bool:
        return self._is_on


@dataclass(init=False)
class CountdownUpdate(SettingsUpdate):
    _output: OutputId
    _enabled: bool
    _total: int
    _remaining: int

    def __init__(self, output: OutputId, enabled: bool, total: int,
                 remaining: int):
        self._output = output
        self._enabled = enabled
        self._total = total
        self._remaining = remaining

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining(self) -> int:
        return self._remaining


@dataclass(init=False)
class ScheduleUpdate(SettingsUpdate):
    _output: OutputId
    _slot: ScheduleSlot
    _schedule: PortSchedule

    def __init__(self, output: OutputId, slot: ScheduleSlot,
                 schedule: PortSchedule):
        self._output = output
        self._slot = slot
        self._schedule = schedule

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def slot(self) -> ScheduleSlot:
        return self._slot

    @property
    def schedule(self) -> PortSchedule:
        return self._schedule


@dataclass(init=False)
class ValueUpdate(SettingsUpdate):
    """A single-value setting change."""

    _field: SettingField
    _value: int

    def __init__(self, field: SettingField, value: int):
        self._field = field
        self._value = value

    @property
    def field(self) -> SettingField:
        return self._field

    @property
    def value(self) -> int:
        return self._value


@dataclass(init=False)
class FirmwareVersionUpdate(SettingsUpdate):
    _version: str

    def __init__(self, version: str):
        self._version = version

    @property
    def version(self) -> str:
        return self._version


@dataclass(init=False)
class ClockUpdate(SettingsUpdate):
    """Clock state change; ``image_time_display`` only from theme status."""

    _clock: ClockState
    _image_time_display: bool | None

    def __init__(self, clock: ClockState,
                 image_time_display: bool | None = None):
        self._clock = clock
        self._image_time_display = image_time_display

    @property
    def clock(self) -> ClockState:
        return self._clock

    @property
    def image_time_display(self) -> bool | None:
        return self._image_time_display


@dataclass(init=False)
class ChargingModeUpdate(SettingsUpdate):
    _code: int
    _profile: CustomProfile | None
    _priority: int | None

    def __init__(self, code: int, profile: CustomProfile | None = None,
                 priority: int | None = None):
        self._code = code
        self._profile = profile
        self._priority = priority

    @property
    def code(self) -> int:
        return self._code

    @property
    def profile(self) -> CustomProfile | None:
        return self._profile

    @property
    def priority(self) -> int | None:
        """Bitmap of preferred C ports, bit 0 = C1."""

        return self._priority


@dataclass(init=False)
class PriorityUpdate(SettingsUpdate):
    """Preferred-port change: a full bitmap or a single output."""

    _bitmap: int | None
    _output: OutputId | None
    _preferred: bool | None

    def __init__(self, *, bitmap: int | None = None,
                 output: OutputId | None = None,
                 preferred: bool | None = None):
        self._bitmap = bitmap
        self._output = output
        self._preferred = preferred

    @property
    def bitmap(self) -> int | None:
        return self._bitmap

    @property
    def output(self) -> OutputId | None:
        return self._output

    @property
    def preferred(self) -> bool | None:
        return self._preferred


@dataclass(init=False)
class ThemeDownloadUpdate(SettingsUpdate):
    _theme_id: int | None
    _status: int
    _theme_code: int | None

    def __init__(self, theme_id: int | None, status: int,
                 theme_code: int | None):
        self._theme_id = theme_id
        self._status = status
        self._theme_code = theme_code

    @property
    def theme_id(self) -> int | None:
        return self._theme_id

    @property
    def status(self) -> int:
        """1 when the download finished."""

        return self._status

    @property
    def theme_code(self) -> int | None:
        return self._theme_code


@dataclass(init=False)
class SettingsChange(Message):
    """Decoded change message: the updates it carries, possibly none."""

    _message_type: int
    _updates: tuple[SettingsUpdate, ...]

    def __init__(self, message_type: int, updates: Iterable[SettingsUpdate]):
        self._message_type = message_type
        self._updates = tuple(updates)

    @property
    def message_type(self) -> int:
        return self._message_type

    @property
    def updates(self) -> tuple[SettingsUpdate, ...]:
        return self._updates


@dataclass(init=False)
class WriteAcknowledgement(Message):
    """Answer to a write (``0x0ANN``); status 0 = accepted, 4 = rejected."""

    _message_type: int
    _status: int | None
    _output: OutputId | None
    _slot: ScheduleSlot | None

    def __init__(self, message_type: int, status: int | None, *,
                 output: OutputId | None = None,
                 slot: ScheduleSlot | None = None):
        self._message_type = message_type
        self._status = status
        self._output = output
        self._slot = slot

    @property
    def message_type(self) -> int:
        return self._message_type

    @property
    def status(self) -> int | None:
        return self._status

    @property
    def output(self) -> OutputId | None:
        """Output echoed by switch, countdown and schedule acks."""

        return self._output

    @property
    def slot(self) -> ScheduleSlot | None:
        return self._slot

    @property
    def rejected(self) -> bool:
        return (self._status or 0) != 0


class SettingsReader:
    """Field access with the official app's lenient value reading."""

    _frame: Frame

    def __init__(self, frame: Frame):
        self._frame = frame

    def value(self, tag: int) -> bytes | None:
        """Payload of the first field with the tag, type byte dropped."""

        field = self._frame.field(tag)
        if field is None or len(field.raw) <= 1:
            return None
        return field.raw[1:]

    def byte(self, tag: int) -> int | None:
        value = self.value(tag)
        return value[0] if value else None

    def integer(self, tag: int) -> int | None:
        """Little-endian integer of one to four payload bytes."""

        value = self.value(tag)
        if not value or len(value) > 4:
            return None
        return int.from_bytes(value, 'little')

    def string(self, tag: int) -> str | None:
        if self._frame.field(tag) is None:
            return None
        value = (self.value(tag) or b'').rstrip(b'\x00')
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return None


class DeviceSettings:
    """Everything controllable the charger reports, merged over time.

    None means not reported yet or, for capability-gated fields, not
    supported by the firmware.
    """

    _firmware_version: str | None
    _error_code: int | None
    _outputs: dict[OutputId, OutputControl]
    _clock: ClockState | None
    _image_time_display: bool | None
    _screen_timeout: int | None
    _charging_mode: int | None
    _updating_firmware: bool | None
    _brightness: int | None
    _knob_direction: int | None
    _hour_format: int | None
    _protocol_version: int | None
    _capabilities: Capabilities | None
    _custom_profile: CustomProfile | None
    _clock_schedule: ClockWindow | None
    _port_protocols: tuple[PortProtocol, ...] | None
    _max_compatibility: bool | None
    _ufcs: bool | None
    _device_identification: bool | None
    _usb_identities: tuple[IdentitySlot, ...]
    _brand_identities: tuple[IdentitySlot, ...]

    def __init__(self) -> None:
        self._firmware_version = None
        self._error_code = None
        self._outputs = {}
        self._clock = None
        self._image_time_display = None
        self._screen_timeout = None
        self._charging_mode = None
        self._updating_firmware = None
        self._brightness = None
        self._knob_direction = None
        self._hour_format = None
        self._protocol_version = None
        self._capabilities = None
        self._custom_profile = None
        self._clock_schedule = None
        self._port_protocols = None
        self._max_compatibility = None
        self._ufcs = None
        self._device_identification = None
        self._usb_identities = ()
        self._brand_identities = ()

    @property
    def firmware_version(self) -> str | None:
        return self._firmware_version

    @property
    def error_code(self) -> int | None:
        """Code 1 = over-temperature; bits 1–4 = C1–C4, bit 5 = USB-A."""

        return self._error_code

    @property
    def outputs(self) -> Mapping[OutputId, OutputControl]:
        return MappingProxyType(self._outputs)

    @property
    def clock(self) -> ClockState | None:
        return self._clock

    @property
    def image_time_display(self) -> bool | None:
        """Time shown over a custom clock picture (from theme status only)."""

        return self._image_time_display

    @property
    def screen_timeout(self) -> int | None:
        return self._screen_timeout

    @property
    def charging_mode(self) -> int | None:
        return self._charging_mode

    @property
    def updating_firmware(self) -> bool | None:
        return self._updating_firmware

    @property
    def brightness(self) -> int | None:
        return self._brightness

    @property
    def knob_direction(self) -> int | None:
        return self._knob_direction

    @property
    def hour_format(self) -> int | None:
        return self._hour_format

    @property
    def protocol_version(self) -> int | None:
        return self._protocol_version

    @property
    def capabilities(self) -> Capabilities | None:
        return self._capabilities

    @property
    def custom_profile(self) -> CustomProfile | None:
        return self._custom_profile

    @property
    def clock_schedule(self) -> ClockWindow | None:
        return self._clock_schedule

    @property
    def port_protocols(self) -> tuple[PortProtocol, ...] | None:
        return self._port_protocols

    @property
    def max_compatibility(self) -> bool | None:
        return self._max_compatibility

    @property
    def ufcs(self) -> bool | None:
        return self._ufcs

    @property
    def device_identification(self) -> bool | None:
        return self._device_identification

    @property
    def usb_identities(self) -> tuple[IdentitySlot, ...]:
        return self._usb_identities

    @property
    def brand_identities(self) -> tuple[IdentitySlot, ...]:
        return self._brand_identities

    @property
    def custom_charging_mode(self) -> bool:
        return self._charging_mode == CUSTOM_CHARGING_MODE_CODE

    @property
    def has_fault(self) -> bool:
        """Codes 1–63 are faults; 0 and 64+ are not, as in the official app."""

        return self._error_code is not None and 1 <= self._error_code <= 63

    @property
    def over_temperature(self) -> bool:
        return self._error_code == OVER_TEMPERATURE_CODE

    @property
    def priority_bitmap(self) -> int:
        """Preferred C ports, bit 0 = C1."""

        bitmap = 0
        for index, output in enumerate(USB_C_GROUPS):
            control = self._outputs.get(output)
            if control is not None and control.priority == PRIORITY_PREFERRED:
                bitmap |= 1 << index
        return bitmap

    def supports(self, capability: Capabilities) -> bool:
        return (self._capabilities is not None
                and capability in self._capabilities)

    def output_fault(self, output: OutputId) -> bool:
        if not self.has_fault or self.over_temperature:
            return False
        bit = 1 << (GROUPS.index(output) + 1)
        return bool((self._error_code or 0) & bit)

    def keep_theme_details(self, previous: DeviceSettings) -> None:
        """Carry over what only the ``0x0A02`` theme status reports.

        A snapshot lacks the theme URL, the holiday display and the custom
        picture time display; the URL is kept while the theme is unchanged.
        """

        if self._image_time_display is None:
            self._image_time_display = previous.image_time_display
        clock = self._clock
        old = previous.clock
        if clock is None or old is None:
            return
        current = (clock.theme_code, clock.theme_id)
        if current == (old.theme_code, old.theme_id):
            clock.set_theme_url(old.theme_url)
        clock.set_showing_holiday(old.showing_holiday)

    def apply(self, update: SettingsUpdate) -> None:
        """Merge one change; fields the change does not carry are kept."""

        if isinstance(update, OutputSwitchUpdate):
            self._apply_switch(update)
        elif isinstance(update, CountdownUpdate):
            self._apply_countdown(update)
        elif isinstance(update, ScheduleUpdate):
            self._apply_schedule(update)
        elif isinstance(update, ValueUpdate):
            self._apply_value(update)
        elif isinstance(update, FirmwareVersionUpdate):
            self._firmware_version = update.version
        elif isinstance(update, ClockUpdate):
            self._apply_clock(update)
        elif isinstance(update, ChargingModeUpdate):
            self._apply_charging_mode(update)
        elif isinstance(update, PriorityUpdate):
            self._apply_priority(update)
        elif isinstance(update, ThemeDownloadUpdate):
            self._apply_theme_download(update)

    def _load_snapshot(self, reader: SettingsReader) -> None:
        self._firmware_version = decode_version(reader.integer(TAG_FIRMWARE))
        self._error_code = reader.integer(TAG_ERROR_CODE)
        for index, output in enumerate(GROUPS):
            tag = TAG_FIRST_OUTPUT_CONTROL + index
            control = decode_output_control(reader.value(tag))
            if control is not None:
                self._outputs[output] = control
        self._clock = _decode_snapshot_clock(reader.value(TAG_CLOCK))

        timeout = reader.byte(TAG_SCREEN_TIMEOUT)
        if timeout is not None:
            self._screen_timeout = timeout & 0x0F
        updating = reader.byte(TAG_UPDATING_FIRMWARE)
        if updating is not None:
            self._updating_firmware = updating == 1
        self._charging_mode = reader.byte(TAG_CHARGING_MODE)
        self._brightness = reader.byte(TAG_BRIGHTNESS)
        self._knob_direction = reader.byte(TAG_KNOB_DIRECTION)
        self._hour_format = reader.byte(TAG_HOUR_FORMAT)
        self._protocol_version = reader.byte(TAG_PROTOCOL_VERSION)
        bits = reader.integer(TAG_CAPABILITIES) or 0
        self._capabilities = Capabilities(bits & 0xFFFF)
        self._usb_identities = decode_identities(reader.value(TAG_USB_IDENTITY))
        brands = reader.value(TAG_BRAND_IDENTITY)
        self._brand_identities = decode_identities(brands)
        self._load_gated_fields(reader)

    def _load_gated_fields(self, reader: SettingsReader) -> None:
        if self.supports(Capabilities.CUSTOM_CHARGING_MODE):
            profile = reader.value(TAG_CUSTOM_PROFILE)
            self._custom_profile = decode_custom_profile(profile)
        if self.supports(Capabilities.CLOCK_SCHEDULE):
            window = reader.value(TAG_CLOCK_SCHEDULE)
            self._clock_schedule = decode_clock_window(window)
        if self.supports(Capabilities.PORT_PROTOCOLS):
            protocols = reader.value(TAG_PORT_PROTOCOLS)
            self._port_protocols = decode_port_protocols(protocols)

        compatibility = reader.byte(TAG_MAX_COMPATIBILITY)
        if self.supports(Capabilities.MAX_COMPATIBILITY):
            self._max_compatibility = _switch_value(compatibility, on_value=0)
        if self.supports(Capabilities.UFCS_SWITCH):
            self._ufcs = _switch_value(reader.byte(TAG_UFCS))
        if self.supports(Capabilities.DEVICE_IDENTIFICATION):
            identification = reader.byte(TAG_DEVICE_IDENTIFICATION)
            self._device_identification = _switch_value(identification)

    def _apply_switch(self, update: OutputSwitchUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_switch(update.is_on)

    def _apply_countdown(self, update: CountdownUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_countdown(update.enabled, update.total,
                                  update.remaining)

    def _apply_schedule(self, update: ScheduleUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_schedule(update.slot, update.schedule)

    def _apply_value(self, update: ValueUpdate) -> None:
        if update.field == SettingField.BRIGHTNESS:
            self._brightness = update.value
        elif update.field == SettingField.ERROR_CODE:
            self._error_code = update.value
        elif update.field == SettingField.KNOB_DIRECTION:
            self._knob_direction = update.value
        elif update.field == SettingField.SCREEN_TIMEOUT:
            self._screen_timeout = update.value
        elif update.field == SettingField.HOUR_FORMAT:
            self._hour_format = update.value
        elif update.field == SettingField.MAX_COMPATIBILITY:
            self._max_compatibility = bool(update.value)
        elif update.field == SettingField.DEVICE_IDENTIFICATION:
            self._device_identification = bool(update.value)
        elif update.field == SettingField.UFCS:
            self._ufcs = bool(update.value)

    def _apply_clock(self, update: ClockUpdate) -> None:
        clock = update.clock
        if update.image_time_display is not None:
            self._image_time_display = update.image_time_display
        elif clock.theme_url is None and self._clock is not None:
            clock.set_theme_url(self._clock.theme_url)
        self._clock = clock

    def _apply_charging_mode(self, update: ChargingModeUpdate) -> None:
        self._charging_mode = update.code
        if update.profile is not None:
            self._custom_profile = update.profile
        if update.priority is not None:
            self._apply_priority_bitmap(update.priority)
        if update.code == PORT_PRIORITY_MODE_CODE:
            return
        for output in USB_C_GROUPS:
            control = self._outputs.get(output)
            if control is not None:
                control.set_priority(PRIORITY_OFF)

    def _apply_priority(self, update: PriorityUpdate) -> None:
        if update.bitmap is not None:
            self._apply_priority_bitmap(update.bitmap)
            return
        if update.output is None:
            return
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_priority(PRIORITY_PREFERRED if update.preferred
                                 else PRIORITY_NORMAL)

    def _apply_priority_bitmap(self, bitmap: int) -> None:
        for index, output in enumerate(USB_C_GROUPS):
            control = self._outputs.get(output)
            if control is None:
                continue
            preferred = bitmap & (1 << index)
            control.set_priority(PRIORITY_PREFERRED if preferred
                                 else PRIORITY_NORMAL)

    def _apply_theme_download(self, update: ThemeDownloadUpdate) -> None:
        if update.status != 1:
            return
        clock = self._clock or ClockState(True, False, 0)
        theme_id = clock.theme_id
        if update.theme_id is not None:
            theme_id = update.theme_id
        theme_code = clock.theme_code
        if update.theme_code in THEME_PICTURE_CODES:
            theme_code = update.theme_code
        clock.set_theme(theme_code, theme_id)
        self._clock = clock

    @classmethod
    def from_snapshot(cls, frame: Frame) -> DeviceSettings:
        """Decode every setting of a ``0x0A00`` frame."""

        settings = cls()
        settings._load_snapshot(SettingsReader(frame))
        return settings


def decode_version(value: int | None) -> str | None:
    """Join decimal digits with dots: 0x0840 = 2112 becomes ``2.1.1.2``."""

    if not value:
        return None
    return '.'.join(str(value))


def decode_output_control(value: bytes | None) -> OutputControl | None:
    """Decode a 19-byte AA–AE output control block."""

    if value is None or len(value) < OUTPUT_CONTROL_LENGTH:
        return None
    total = int.from_bytes(value[10:14], 'little')
    remaining = int.from_bytes(value[14:18], 'little')
    return OutputControl(is_on=value[0] == 1,
                         power_on=PortSchedule(*value[1:5]),
                         power_off=PortSchedule(*value[5:9]),
                         countdown_enabled=value[9] == 1, countdown_total=total,
                         countdown_remaining=remaining, priority=value[18])


def decode_custom_profile(value: bytes | None) -> CustomProfile | None:
    if value is None or len(value) < 7:
        return None
    return CustomProfile(value[0], value[1] == 1, value[2:7])


def decode_clock_window(value: bytes | None) -> ClockWindow | None:
    if value is None or len(value) < 5:
        return None
    return ClockWindow(*value[:5])


def decode_port_protocols(value: bytes | None
                          ) -> tuple[PortProtocol, ...] | None:
    """Flags of C1–C4 from three-byte groups; only the low six bits count."""

    if value is None or len(value) < 12:
        return None
    return tuple(PortProtocol(value[index * 3] & 0x3F) for index in range(4))


def decode_identities(value: bytes | None) -> tuple[IdentitySlot, ...]:
    if value is None or len(value) < 16:
        return ()
    slots = []
    for index, output in enumerate(USB_C_GROUPS):
        first = int.from_bytes(value[index * 4:index * 4 + 2], 'little')
        second = int.from_bytes(value[index * 4 + 2:index * 4 + 4], 'little')
        slots.append(IdentitySlot(output, first, second))
    return tuple(slots)


def decode_settings_change(frame: Frame) -> SettingsChange | None:
    """Decode a change message; None for any other message type."""

    decoder = _CHANGE_DECODERS.get(frame.message_type)
    if decoder is None:
        return None
    update = decoder(SettingsReader(frame))
    updates = [update] if update is not None else []
    return SettingsChange(frame.message_type, updates)


def decode_write_acknowledgement(frame: Frame) -> WriteAcknowledgement | None:
    """Decode a ``0x0ANN`` write ack; None for any other message type."""

    message_type = frame.message_type
    if (message_type & 0xFF00 != ACK_PREFIX
            or message_type in (MSG_STATUS_SNAPSHOT, MSG_THEME_STATUS)):
        return None
    reader = SettingsReader(frame)
    output = None
    slot = None
    if message_type in (ACK_OUTPUT_SWITCH, ACK_SCHEDULE, ACK_COUNTDOWN):
        output = output_of_group(reader.integer(0xA2))
    if message_type == ACK_SCHEDULE:
        slot = decode_value(SCHEDULE_SLOT_CODES, reader.byte(0xA3))
    return WriteAcknowledgement(message_type, frame.increment, output=output,
                                slot=slot)


def _switch_value(value: int | None, on_value: int = 1) -> bool | None:
    return value == on_value if value is not None else None


def _decode_snapshot_clock(value: bytes | None) -> ClockState | None:
    if not value:
        return None
    theme_id = None
    if len(value) >= 5:
        theme_id = int.from_bytes(value[1:5], 'little')
    return ClockState.from_flags(value[0], theme_id=theme_id)


def _decode_clock(reader: SettingsReader) -> ClockState | None:
    flags = reader.byte(0xA2)
    if flags is None:
        return None
    return ClockState.from_flags(flags, theme_id=reader.integer(0xA3),
                                 theme_url=reader.string(0xA4) or None,
                                 showing_holiday=reader.byte(0xA5) == 1)


def _firmware_change(reader: SettingsReader) -> SettingsUpdate | None:
    version = decode_version(reader.integer(0xA2))
    return FirmwareVersionUpdate(version) if version else None


def _output_change(reader: SettingsReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    state = reader.byte(0xA3)
    if output is None or state is None:
        return None
    return OutputSwitchUpdate(output, state == 1)


def _error_code_change(reader: SettingsReader) -> SettingsUpdate | None:
    code = reader.integer(0xA2)
    if code is None:
        return None
    return ValueUpdate(SettingField.ERROR_CODE, code & 0xFFFF)


def _countdown_change(reader: SettingsReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    value = reader.value(0xA3)
    if output is None or value is None or len(value) < 9:
        return None
    total = int.from_bytes(value[1:5], 'little')
    remaining = int.from_bytes(value[5:9], 'little')
    return CountdownUpdate(output, value[0] == 1, total, remaining)


def _schedule_change(reader: SettingsReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    slot = decode_value(SCHEDULE_SLOT_CODES, reader.byte(0xA3))
    value = reader.value(0xA4)
    if output is None or slot is None or value is None or len(value) < 4:
        return None
    return ScheduleUpdate(output, slot, PortSchedule(*value[:4]))


def _screen_timeout_change(reader: SettingsReader) -> SettingsUpdate | None:
    code = reader.byte(0xA2)
    if code is None:
        return None
    return ValueUpdate(SettingField.SCREEN_TIMEOUT, code & 0x0F)


def _single_value_change(field: SettingField
                         ) -> Callable[[SettingsReader], SettingsUpdate | None]:
    def decode(reader: SettingsReader) -> SettingsUpdate | None:
        value = reader.byte(0xA2)
        return ValueUpdate(field, value) if value is not None else None

    return decode


def _clock_change(reader: SettingsReader) -> SettingsUpdate | None:
    clock = _decode_clock(reader)
    return ClockUpdate(clock) if clock is not None else None


def _theme_status(reader: SettingsReader) -> SettingsUpdate | None:
    clock = _decode_clock(reader)
    if clock is None:
        return None
    return ClockUpdate(clock, reader.byte(0xA6) == 1)


def _charging_mode_change(reader: SettingsReader) -> SettingsUpdate | None:
    code = reader.byte(0xA2)
    if code is None:
        return None
    profile = decode_custom_profile(reader.value(0xA3))
    return ChargingModeUpdate(code, profile, reader.byte(0xA4))


def _priority_change(reader: SettingsReader) -> SettingsUpdate | None:
    bitmap = reader.byte(0xA3)
    if bitmap is not None:
        return PriorityUpdate(bitmap=bitmap)
    value = reader.value(0xA2)
    if value is None or len(value) < 2:
        return None
    output = output_of_group(value[0])
    if output is None or output not in USB_C_GROUPS:
        return None
    return PriorityUpdate(output=output, preferred=value[1] == 1)


def _theme_download_change(reader: SettingsReader) -> SettingsUpdate | None:
    status = reader.integer(0xA3)
    if status is None:
        return None
    return ThemeDownloadUpdate(reader.integer(0xA2), status & 0xFF,
                               reader.byte(0xA4))


_CHANGE_DECODERS = {
    MSG_FIRMWARE_VERSION_CHANGE: _firmware_change,
    MSG_OUTPUT_CHANGE: _output_change,
    MSG_ERROR_CODE_CHANGE: _error_code_change,
    MSG_COUNTDOWN_CHANGE: _countdown_change,
    MSG_SCHEDULE_CHANGE: _schedule_change,
    MSG_SCREEN_TIMEOUT_CHANGE: _screen_timeout_change,
    MSG_BRIGHTNESS_CHANGE: _single_value_change(SettingField.BRIGHTNESS),
    MSG_CLOCK_CHANGE: _clock_change,
    MSG_CHARGING_MODE_CHANGE: _charging_mode_change,
    MSG_PRIORITY_CHANGE: _priority_change,
    MSG_KNOB_DIRECTION_CHANGE: _single_value_change(SettingField.KNOB_DIRECTION),
    MSG_THEME_DOWNLOAD_CHANGE: _theme_download_change,
    MSG_THEME_STATUS: _theme_status,
}
