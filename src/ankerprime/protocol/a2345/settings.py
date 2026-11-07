"""A2345 settings: wire constants, value types and their decoders.

Change messages and acknowledgements are in ``updates``, the snapshot model
in ``device_settings``; ``fields.FieldReader`` reads values the way the
official app does.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag, StrEnum

from ankerprime.model.enums import OutputId, ScheduleSlot
from ankerprime.protocol.a2345.codes import USB_C_GROUPS


ACK_PREFIX = 0x0A00
CUSTOM_CHARGING_MODE_CODE = 5
PORT_PRIORITY_MODE_CODE = 2
OVER_TEMPERATURE_CODE = 1
SCHEDULE_UNSET = 0xFF
OUTPUT_CONTROL_LENGTH = 19
THEME_PICTURE_CODES = (3, 5)


class SnapshotTag(IntEnum):
    """Field tags of the ``0x0A00`` status snapshot.

    The six port readings follow ``FIRST_PORT``; the five output controls
    ``FIRST_OUTPUT_CONTROL``.
    """

    FIRMWARE = 0xA2
    ERROR_CODE = 0xA3
    FIRST_PORT = 0xA4
    FIRST_OUTPUT_CONTROL = 0xAA
    CLOCK = 0xAF
    SCREEN_TIMEOUT = 0xB0
    CHARGING_MODE = 0xB1
    UPDATING_FIRMWARE = 0xB2
    BRIGHTNESS = 0xB3
    KNOB_DIRECTION = 0xB4
    HOUR_FORMAT = 0xB5
    PROTOCOL_VERSION = 0xB6
    CAPABILITIES = 0xB7
    CUSTOM_PROFILE = 0xB8
    CLOCK_SCHEDULE = 0xB9
    PORT_PROTOCOLS = 0xBA
    MAX_COMPATIBILITY = 0xBB
    UFCS = 0xBC
    USB_IDENTITY = 0xBD
    BRAND_IDENTITY = 0xBE
    DEVICE_IDENTIFICATION = 0xBF


class PriorityCode(IntEnum):
    """Port priority of one output on the wire."""

    OFF = 0
    NORMAL = 1
    PREFERRED = 2


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


def switch_value(value: int | None, on_value: int = 1) -> bool | None:
    return value == on_value if value is not None else None


def decode_snapshot_clock(value: bytes | None) -> ClockState | None:
    if not value:
        return None
    theme_id = None
    if len(value) >= 5:
        theme_id = int.from_bytes(value[1:5], 'little')
    return ClockState.from_flags(value[0], theme_id=theme_id)
