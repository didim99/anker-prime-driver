"""A2345 change messages and write acknowledgements.

A change message carries ``SettingsUpdate``s for ``DeviceSettings.apply``;
an acknowledgement answers a write of this or another client.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ankerprime.model.enums import OutputId, ScheduleSlot
from ankerprime.protocol.a2345.codes import (
    SCHEDULE_SLOT_CODES, USB_C_GROUPS, output_of_group)
from ankerprime.protocol.a2345.fields import FieldReader
from ankerprime.protocol.a2345.frame import Frame, Message, MessageType
from ankerprime.protocol.a2345.settings import (
    ACK_PREFIX, ClockState, CustomProfile, PortSchedule,
    SettingField, decode_custom_profile, decode_version)
from ankerprime.protocol.codes import decode_value


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


def decode_settings_change(frame: Frame) -> SettingsChange | None:
    """Decode a change message; None for any other message type."""

    decoder = _CHANGE_DECODERS.get(frame.message_type)
    if decoder is None:
        return None
    update = decoder(FieldReader(frame))
    updates = [update] if update is not None else []
    return SettingsChange(frame.message_type, updates)


def decode_write_acknowledgement(frame: Frame) -> WriteAcknowledgement | None:
    """Decode a ``0x0ANN`` write ack; None for any other message type."""

    message_type = frame.message_type
    if (message_type & 0xFF00 != ACK_PREFIX
            or message_type in (MessageType.STATUS_SNAPSHOT,
                                MessageType.THEME_STATUS)):
        return None
    reader = FieldReader(frame)
    output = None
    slot = None
    if message_type in (MessageType.OUTPUT_SWITCH_ACK, MessageType.SCHEDULE_ACK,
                        MessageType.COUNTDOWN_ACK):
        output = output_of_group(reader.integer(0xA2))
    if message_type == MessageType.SCHEDULE_ACK:
        slot = decode_value(SCHEDULE_SLOT_CODES, reader.byte(0xA3))
    return WriteAcknowledgement(message_type, frame.increment, output=output,
                                slot=slot)


def _decode_clock(reader: FieldReader) -> ClockState | None:
    flags = reader.byte(0xA2)
    if flags is None:
        return None
    return ClockState.from_flags(flags, theme_id=reader.integer(0xA3),
                                 theme_url=reader.string(0xA4) or None,
                                 showing_holiday=reader.byte(0xA5) == 1)


def _firmware_change(reader: FieldReader) -> SettingsUpdate | None:
    version = decode_version(reader.integer(0xA2))
    return FirmwareVersionUpdate(version) if version else None


def _output_change(reader: FieldReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    state = reader.byte(0xA3)
    if output is None or state is None:
        return None
    return OutputSwitchUpdate(output, state == 1)


def _error_code_change(reader: FieldReader) -> SettingsUpdate | None:
    code = reader.integer(0xA2)
    if code is None:
        return None
    return ValueUpdate(SettingField.ERROR_CODE, code & 0xFFFF)


def _countdown_change(reader: FieldReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    value = reader.value(0xA3)
    if output is None or value is None or len(value) < 9:
        return None
    total = int.from_bytes(value[1:5], 'little')
    remaining = int.from_bytes(value[5:9], 'little')
    return CountdownUpdate(output, value[0] == 1, total, remaining)


def _schedule_change(reader: FieldReader) -> SettingsUpdate | None:
    output = output_of_group(reader.integer(0xA2))
    slot = decode_value(SCHEDULE_SLOT_CODES, reader.byte(0xA3))
    value = reader.value(0xA4)
    if output is None or slot is None or value is None or len(value) < 4:
        return None
    return ScheduleUpdate(output, slot, PortSchedule(*value[:4]))


def _screen_timeout_change(reader: FieldReader) -> SettingsUpdate | None:
    code = reader.byte(0xA2)
    if code is None:
        return None
    return ValueUpdate(SettingField.SCREEN_TIMEOUT, code & 0x0F)


def _single_value_change(field: SettingField
                         ) -> Callable[[FieldReader], SettingsUpdate | None]:
    def decode(reader: FieldReader) -> SettingsUpdate | None:
        value = reader.byte(0xA2)
        return ValueUpdate(field, value) if value is not None else None

    return decode


def _clock_change(reader: FieldReader) -> SettingsUpdate | None:
    clock = _decode_clock(reader)
    return ClockUpdate(clock) if clock is not None else None


def _theme_status(reader: FieldReader) -> SettingsUpdate | None:
    clock = _decode_clock(reader)
    if clock is None:
        return None
    return ClockUpdate(clock, reader.byte(0xA6) == 1)


def _charging_mode_change(reader: FieldReader) -> SettingsUpdate | None:
    code = reader.byte(0xA2)
    if code is None:
        return None
    profile = decode_custom_profile(reader.value(0xA3))
    return ChargingModeUpdate(code, profile, reader.byte(0xA4))


def _priority_change(reader: FieldReader) -> SettingsUpdate | None:
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


def _theme_download_change(reader: FieldReader) -> SettingsUpdate | None:
    status = reader.integer(0xA3)
    if status is None:
        return None
    return ThemeDownloadUpdate(reader.integer(0xA2), status & 0xFF,
                               reader.byte(0xA4))


_CHANGE_DECODERS: dict[int, Callable[[FieldReader], SettingsUpdate | None]] = {
    MessageType.FIRMWARE_VERSION_CHANGE: _firmware_change,
    MessageType.OUTPUT_CHANGE: _output_change,
    MessageType.ERROR_CODE_CHANGE: _error_code_change,
    MessageType.COUNTDOWN_CHANGE: _countdown_change,
    MessageType.SCHEDULE_CHANGE: _schedule_change,
    MessageType.SCREEN_TIMEOUT_CHANGE: _screen_timeout_change,
    MessageType.BRIGHTNESS_CHANGE: _single_value_change(SettingField.BRIGHTNESS),
    MessageType.CLOCK_CHANGE: _clock_change,
    MessageType.CHARGING_MODE_CHANGE: _charging_mode_change,
    MessageType.PRIORITY_CHANGE: _priority_change,
    MessageType.KNOB_DIRECTION_CHANGE:
        _single_value_change(SettingField.KNOB_DIRECTION),
    MessageType.THEME_DOWNLOAD_CHANGE: _theme_download_change,
    MessageType.THEME_STATUS: _theme_status,
}
