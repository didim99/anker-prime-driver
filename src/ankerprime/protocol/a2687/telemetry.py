"""Decoding of A2687 state reports.

The realtime report (0x0300, about 1 Hz once triggered) and the read-all
reply (0x0200) share one field set:

- A5/A6/A7 (C1..C3): byte block ``status, mV, mA, cW`` (u16 LE each).
- AC/AD/AE (C1..C3): 12-byte control block ``switch, countdown status,
  total u32, remaining u32, cable, profile``.
- B4: three ``VID, PID`` pairs (u16 LE), C1..C3; FFFA:FFFB means none.
- A8 screen timeout, A9 brightness, AA charging mode, AF orientation, B2
  auto-rotation: one byte each, bare or typed. Only read-all carries them.

Field meanings follow Charker, settled on its owner's charger (firmware
v0.0.5.2). Values outside the observed ranges are dropped, not guessed.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Self, TypeVar

from ankerprime.model.enums import PortId
from ankerprime.protocol.a2687.codes import (
    CONTROL_FIELDS, FIELD_BRIGHTNESS, FIELD_CHARGING_MODE,
    FIELD_CONNECTED_DEVICES, FIELD_COVER_STATE, FIELD_GYROSCOPE,
    FIELD_ORIENTATION, FIELD_SCREEN_TIMEOUT, PORT_FIELDS, PORTS)
from ankerprime.protocol.a2687.tlv import (
    TYPE_BYTES, TYPE_U8, Payload, TypedValue)


DecodedT = TypeVar('DecodedT')

MAX_SCREEN_TIMEOUT = 4
MAX_BRIGHTNESS = 100
MAX_ORIENTATION = 3
SETTING_FIELDS = (FIELD_SCREEN_TIMEOUT, FIELD_BRIGHTNESS, FIELD_CHARGING_MODE,
                  FIELD_ORIENTATION, FIELD_GYROSCOPE, FIELD_COVER_STATE)


@dataclass(init=False)
class RawPortTelemetry:
    """Electrical reading of one port; the status is non-zero while on."""

    LENGTH: ClassVar[int] = 7

    _status: int
    _voltage: float
    _current: float
    _power: float

    def __init__(self, status: int, voltage: float, current: float,
                 power: float):
        self._status = status
        self._voltage = voltage
        self._current = current
        self._power = power

    @property
    def status(self) -> int:
        return self._status

    @property
    def voltage(self) -> float:
        """Volts."""

        return self._voltage

    @property
    def current(self) -> float:
        """Amperes."""

        return self._current

    @property
    def power(self) -> float:
        """Watts."""

        return self._power

    @property
    def is_on(self) -> bool:
        return self._status != 0

    @classmethod
    def decode(cls, block: bytes) -> Self | None:
        if len(block) < cls.LENGTH:
            return None
        return cls(block[0], _u16(block, 1) / 1000, _u16(block, 3) / 1000,
                   _u16(block, 5) / 100)


@dataclass(init=False)
class PortControl:
    """Switch, countdown, cable and vendor profile of one port."""

    LENGTH: ClassVar[int] = 12
    COUNTDOWN_RUNNING: ClassVar[int] = 1
    MAX_COUNTDOWN: ClassVar[int] = 86_400
    NO_CABLE: ClassVar[int] = 3

    _switch_status: int
    _countdown_status: int
    _countdown_total: int
    _countdown_remaining: int
    _cable_code: int
    _profile_code: int

    def __init__(self, switch_status: int, countdown_status: int,
                 countdown_total: int, countdown_remaining: int,
                 cable_code: int, profile_code: int):
        self._switch_status = switch_status
        self._countdown_status = countdown_status
        self._countdown_total = countdown_total
        self._countdown_remaining = countdown_remaining
        self._cable_code = cable_code
        self._profile_code = profile_code

    @property
    def switch_status(self) -> int:
        return self._switch_status

    @property
    def countdown_status(self) -> int:
        return self._countdown_status

    @property
    def countdown_total(self) -> int:
        """Seconds."""

        return self._countdown_total

    @property
    def countdown_remaining(self) -> int:
        """Seconds."""

        return self._countdown_remaining

    @property
    def cable_code(self) -> int:
        return self._cable_code

    @property
    def profile_code(self) -> int:
        return self._profile_code

    @property
    def countdown_running(self) -> bool:
        """A countdown is armed; idle ports report ``300/300`` with status 0."""

        return (self._countdown_status == self.COUNTDOWN_RUNNING
                and 0 < self._countdown_remaining <= self._countdown_total
                <= self.MAX_COUNTDOWN)

    @classmethod
    def decode(cls, block: bytes) -> Self | None:
        if len(block) < cls.LENGTH:
            return None
        return cls(block[0], block[1], _u32(block, 2), _u32(block, 6),
                   block[10], block[11])


@dataclass(init=False)
class UsbIdentity:
    """USB vendor and product ID of the device on a port."""

    EMPTY_VENDOR: ClassVar[int] = 0xFFFA
    EMPTY_PRODUCT: ClassVar[int] = 0xFFFB

    _vendor_id: int
    _product_id: int

    def __init__(self, vendor_id: int, product_id: int):
        self._vendor_id = vendor_id
        self._product_id = product_id

    @property
    def vendor_id(self) -> int:
        return self._vendor_id

    @property
    def product_id(self) -> int:
        return self._product_id

    @property
    def empty(self) -> bool:
        """Nothing identified: the explicit marker or all zeros."""

        return ((self._vendor_id, self._product_id)
                in ((self.EMPTY_VENDOR, self.EMPTY_PRODUCT), (0, 0)))


@dataclass(init=False)
class DeviceSettings:
    """Settings found in one report; None for fields it lacked."""

    _screen_timeout: int | None
    _brightness: int | None
    _charging_mode: int | None
    _orientation: int | None
    _auto_rotation: bool | None
    _screen_picture: int | None

    def __init__(self, *, screen_timeout: int | None = None,
                 brightness: int | None = None,
                 charging_mode: int | None = None,
                 orientation: int | None = None,
                 auto_rotation: bool | None = None,
                 screen_picture: int | None = None):
        self._screen_timeout = screen_timeout
        self._brightness = brightness
        self._charging_mode = charging_mode
        self._orientation = orientation
        self._auto_rotation = auto_rotation
        self._screen_picture = screen_picture

    @property
    def screen_timeout(self) -> int | None:
        return self._screen_timeout

    @property
    def brightness(self) -> int | None:
        """Percent."""

        return self._brightness

    @property
    def charging_mode(self) -> int | None:
        return self._charging_mode

    @property
    def orientation(self) -> int | None:
        return self._orientation

    @property
    def auto_rotation(self) -> bool | None:
        return self._auto_rotation

    @property
    def screen_picture(self) -> int | None:
        """Low 16 bits of the id of the screen picture shown (``E1``)."""

        return self._screen_picture


@dataclass(init=False)
class Report:
    """Everything one report carried, per port where applicable."""

    _ports: Mapping[PortId, RawPortTelemetry]
    _controls: Mapping[PortId, PortControl]
    _identities: Mapping[PortId, UsbIdentity]
    _settings: DeviceSettings | None

    def __init__(self, *, ports: Mapping[PortId, RawPortTelemetry],
                 controls: Mapping[PortId, PortControl] | None = None,
                 identities: Mapping[PortId, UsbIdentity] | None = None,
                 settings: DeviceSettings | None = None):
        self._ports = MappingProxyType(dict(ports))
        self._controls = MappingProxyType(dict(controls or {}))
        self._identities = MappingProxyType(dict(identities or {}))
        self._settings = settings

    @property
    def ports(self) -> Mapping[PortId, RawPortTelemetry]:
        return self._ports

    @property
    def controls(self) -> Mapping[PortId, PortControl]:
        return self._controls

    @property
    def identities(self) -> Mapping[PortId, UsbIdentity]:
        return self._identities

    @property
    def settings(self) -> DeviceSettings | None:
        """None when the report carried no setting field."""

        return self._settings

    @property
    def empty(self) -> bool:
        return not self._ports and self._settings is None


def decode_report(payload: Payload) -> Report:
    """Decode the state fields of any payload; unknown fields are ignored.

    Acknowledgements and bind replies decode to an empty report.
    """

    ports = {}
    controls = {}
    for port, field, control_field in zip(PORTS, PORT_FIELDS, CONTROL_FIELDS,
                                          strict=True):
        telemetry = _decode_block(payload, field, RawPortTelemetry.decode)
        if telemetry is None:
            continue
        ports[port] = telemetry
        control = _decode_block(payload, control_field, PortControl.decode)
        if control is not None:
            controls[port] = control
    return Report(ports=ports, controls=controls,
                  identities=_identities(payload),
                  settings=_settings(payload))


def _identities(payload: Payload) -> dict[PortId, UsbIdentity]:
    raw = payload.get(FIELD_CONNECTED_DEVICES)
    if raw is None:
        return {}
    block = raw
    value = TypedValue.decode(raw)
    if value is not None and value.block is not None:
        block = value.block
    identities = {}
    for index, port in enumerate(PORTS):
        offset = index * 4
        if offset + 4 > len(block):
            break
        identities[port] = UsbIdentity(_u16(block, offset),
                                       _u16(block, offset + 2))
    return identities


def _settings(payload: Payload) -> DeviceSettings | None:
    if not any(payload.get(field) is not None for field in SETTING_FIELDS):
        return None
    timeout = _setting_byte(payload, FIELD_SCREEN_TIMEOUT, MAX_SCREEN_TIMEOUT)
    brightness = _setting_byte(payload, FIELD_BRIGHTNESS, MAX_BRIGHTNESS)
    orientation = _setting_byte(payload, FIELD_ORIENTATION, MAX_ORIENTATION)
    rotation = _setting_byte(payload, FIELD_GYROSCOPE, 1)
    return DeviceSettings(
        screen_timeout=timeout, brightness=brightness,
        charging_mode=_setting_byte(payload, FIELD_CHARGING_MODE),
        orientation=orientation,
        auto_rotation=None if rotation is None else rotation == 1,
        screen_picture=_screen_picture(payload))


def _screen_picture(payload: Payload) -> int | None:
    """``E1``: bytes 2-3 of a ten-byte block are the picture shown."""

    value = payload.typed(FIELD_COVER_STATE)
    if value is None or len(value.body) < 4:
        return None
    return _u16(value.body, 2)


def _setting_byte(payload: Payload, field: int,
                  maximum: int = 0xFF) -> int | None:
    """A one-byte setting, bare, typed u8 or a one-byte block."""

    raw = payload.get(field)
    if not raw:
        return None
    value = None
    if len(raw) == 1:
        value = raw[0]
    elif len(raw) == 2 and raw[0] in (TYPE_U8, TYPE_BYTES):
        value = raw[1]
    return value if value is not None and value <= maximum else None


def _decode_block(payload: Payload, field: int,
                  decode: Callable[[bytes], DecodedT | None]
                  ) -> DecodedT | None:
    value = payload.typed(field)
    if value is None or value.block is None:
        return None
    return decode(value.block)


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], 'little')


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], 'little')
