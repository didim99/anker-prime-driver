"""Decoders of the A2345 telemetry, snapshot, version and acknowledgement messages.

Raw values are kept as reported; naming and scaling into the shared model
happens in the mapping layer. Fields this library does not interpret are
preserved in ``unknown_fields``.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

from ankerprime.model.enums import PortId
from ankerprime.protocol.a2345.device_settings import DeviceSettings
from ankerprime.protocol.a2345.fields import (
    FieldReader, MessageError, MessageErrorKind)
from ankerprime.protocol.a2345.frame import (
    TAG_SESSION, TAG_TIMESTAMP, Frame, Message, MessageType, Tlv, ValueType)
from ankerprime.protocol.a2345.settings import SnapshotTag
from ankerprime.protocol.a2345.updates import (
    decode_settings_change, decode_write_acknowledgement)


TELEMETRY_ACK_TYPES = frozenset({0x0A0A, MessageType.REALTIME_ACK, 0x0A14})

PORTS = (PortId.C1, PortId.C2, PortId.C3, PortId.C4, PortId.A1, PortId.A2)
USB_C_PORTS = PORTS[:4]


PORT_PAYLOAD_LENGTH = 7
SLOT_LENGTH = 4
ACK_FRAME_LENGTH = 14


class RealtimeTag(IntEnum):
    """Field tags of the ``0x0303`` realtime telemetry."""

    FIRST_PORT = 0xA2
    METADATA = 0xA8
    PROVISIONAL_METADATA = 0xA9


class VersionTag(IntEnum):
    """Field tags of the ``0x0830`` version information."""

    HARDWARE_VERSION = 0xA1
    SOFTWARE_VERSION = 0xA2
    PRODUCT_CODE = 0xA3
    MCU_COMPONENT = 0xA4
    ESP32_COMPONENT = 0xA5


@dataclass(init=False)
class RawPortReading:
    """Port readings as reported, plus the mainland firmware tail bytes.

    Tail codes: cable 0 = 3 A, 1 = 5 A, 2 = EPR, 3 = Huawei, 4 = Xiaomi;
    fast charge 0 = none, 1 = Huawei SCP, 2 = Xiaomi HyperCharge;
    device brand 0 = unknown, 1 = Huawei/Honor, 2 = Xiaomi.
    """

    _status: int
    _millivolts: int
    _milliamps: int
    _centiwatts: int
    _cable: int | None
    _fast_charge: int | None
    _device_brand: int | None

    def __init__(self, status: int, millivolts: int, milliamps: int,
                 centiwatts: int, *, cable: int | None = None,
                 fast_charge: int | None = None,
                 device_brand: int | None = None):
        self._status = status
        self._millivolts = millivolts
        self._milliamps = milliamps
        self._centiwatts = centiwatts
        self._cable = cable
        self._fast_charge = fast_charge
        self._device_brand = device_brand

    @property
    def status(self) -> int:
        """Raw status; only 0 (idle) and 1 (active) are established."""

        return self._status

    @property
    def millivolts(self) -> int:
        return self._millivolts

    @property
    def milliamps(self) -> int:
        return self._milliamps

    @property
    def centiwatts(self) -> int:
        return self._centiwatts

    @property
    def voltage(self) -> float:
        return self._millivolts / 1000

    @property
    def current(self) -> float:
        return self._milliamps / 1000

    @property
    def power(self) -> float:
        return self._centiwatts / 100

    @property
    def cable(self) -> int | None:
        return self._cable

    @property
    def fast_charge(self) -> int | None:
        return self._fast_charge

    @property
    def device_brand(self) -> int | None:
        return self._device_brand


@dataclass(init=False)
class MetadataSlot:
    """Four raw bytes of one USB-C port from the A8/A9 metadata fields.

    The meaning is not established (A8 word 0 matched a USB vendor id once),
    so the words stay unnamed.
    """

    _port: PortId
    _raw: bytes

    def __init__(self, port: PortId, raw: bytes):
        self._port = port
        self._raw = raw

    @property
    def port(self) -> PortId:
        return self._port

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def word0(self) -> int:
        return int.from_bytes(self._raw[0:2], 'little')

    @property
    def word1(self) -> int:
        return int.from_bytes(self._raw[2:4], 'little')

    @property
    def is_all_ones(self) -> bool:
        return all(byte == 0xFF for byte in self._raw)

    @property
    def is_unidentified(self) -> bool:
        return not any(self._raw)


@dataclass(init=False)
class RealtimeTelemetry(Message):
    """Decoded ``0x0303`` realtime message."""

    _response_marker: int | None
    _ports: Mapping[PortId, RawPortReading]
    _metadata: tuple[MetadataSlot, ...]
    _provisional_metadata: tuple[MetadataSlot, ...] | None
    _timestamp: int | None
    _unknown_fields: tuple[Tlv, ...]

    def __init__(self, *, response_marker: int | None,
                 ports: Mapping[PortId, RawPortReading],
                 metadata: Iterable[MetadataSlot],
                 provisional_metadata: Iterable[MetadataSlot] | None,
                 timestamp: int | None, unknown_fields: Iterable[Tlv]):
        self._response_marker = response_marker
        self._ports = MappingProxyType(dict(ports))
        self._metadata = tuple(metadata)
        self._provisional_metadata = (tuple(provisional_metadata)
                                      if provisional_metadata is not None
                                      else None)
        self._timestamp = timestamp
        self._unknown_fields = tuple(unknown_fields)

    @property
    def response_marker(self) -> int | None:
        return self._response_marker

    @property
    def ports(self) -> Mapping[PortId, RawPortReading]:
        return self._ports

    @property
    def metadata(self) -> tuple[MetadataSlot, ...]:
        """A8 slots for C1–C4; empty when absent or malformed."""

        return self._metadata

    @property
    def provisional_metadata(self) -> tuple[MetadataSlot, ...] | None:
        """A9 slots for C1–C4; None on firmware without A9 or when malformed."""

        return self._provisional_metadata

    @property
    def timestamp(self) -> int | None:
        """Charger epoch seconds."""

        return self._timestamp

    @property
    def unknown_fields(self) -> tuple[Tlv, ...]:
        return self._unknown_fields


@dataclass(init=False)
class StatusSnapshot(Message):
    """Decoded ``0x0A00`` full-device message."""

    _ports: Mapping[PortId, RawPortReading]
    _settings: DeviceSettings
    _unknown_fields: tuple[Tlv, ...]

    def __init__(self, ports: Mapping[PortId, RawPortReading],
                 settings: DeviceSettings, unknown_fields: Iterable[Tlv]):
        self._ports = MappingProxyType(dict(ports))
        self._settings = settings
        self._unknown_fields = tuple(unknown_fields)

    @property
    def ports(self) -> Mapping[PortId, RawPortReading]:
        return self._ports

    @property
    def settings(self) -> DeviceSettings:
        return self._settings

    @property
    def unknown_fields(self) -> tuple[Tlv, ...]:
        """All non-port fields, settings included, in wire form."""

        return self._unknown_fields


@dataclass(init=False)
class VersionInfo(Message):
    """Decoded ``0x0830`` version message."""

    _hardware_version: str
    _software_version: str
    _product_code: str | None
    _mcu_component: str | None
    _esp32_component: str | None
    _unknown_fields: tuple[Tlv, ...]

    def __init__(self, *, hardware_version: str, software_version: str,
                 product_code: str | None, mcu_component: str | None,
                 esp32_component: str | None,
                 unknown_fields: Iterable[Tlv]):
        self._hardware_version = hardware_version
        self._software_version = software_version
        self._product_code = product_code
        self._mcu_component = mcu_component
        self._esp32_component = esp32_component
        self._unknown_fields = tuple(unknown_fields)

    @property
    def hardware_version(self) -> str:
        return self._hardware_version

    @property
    def software_version(self) -> str:
        """Firmware version without a leading ``v``."""

        return self._software_version

    @property
    def product_code(self) -> str | None:
        return self._product_code

    @property
    def mcu_component(self) -> str | None:
        return self._mcu_component

    @property
    def esp32_component(self) -> str | None:
        return self._esp32_component

    @property
    def unknown_fields(self) -> tuple[Tlv, ...]:
        return self._unknown_fields


@dataclass(init=False)
class UnknownMessage(Message):
    """Valid frame of a message type this library does not decode."""

    _frame: Frame

    def __init__(self, frame: Frame):
        self._frame = frame

    @property
    def frame(self) -> Frame:
        return self._frame


@dataclass(init=False)
class Acknowledgement(Message):
    """Decoded 14-byte telemetry acknowledgement; the marker has no known meaning."""

    _message_type: int
    _increment: int | None
    _marker: int

    def __init__(self, message_type: int, increment: int | None, marker: int):
        self._message_type = message_type
        self._increment = increment
        self._marker = marker

    @property
    def message_type(self) -> int:
        return self._message_type

    @property
    def increment(self) -> int | None:
        return self._increment

    @property
    def marker(self) -> int:
        return self._marker


def decode_message(frame: Frame) -> Message:
    """Decode any frame the charger sends.

    Telemetry, snapshot and version messages are decoded strictly; change
    messages and ``0x0ANN`` write acks leniently; anything else is returned
    as ``UnknownMessage``. Telemetry acks decode as write acks here; use
    ``decode_acknowledgement`` for their marker.
    """

    if frame.message_type == MessageType.REALTIME:
        return decode_realtime(frame)
    if frame.message_type == MessageType.STATUS_SNAPSHOT:
        return decode_status_snapshot(frame)
    if frame.message_type == MessageType.VERSION_INFO:
        return decode_version_info(frame)
    change = decode_settings_change(frame)
    if change is not None:
        return change
    acknowledgement = decode_write_acknowledgement(frame)
    if acknowledgement is not None:
        return acknowledgement
    return UnknownMessage(frame)


def decode_realtime(frame: Frame) -> RealtimeTelemetry:
    """Decode a ``0x0303`` frame; optional metadata never fails the message."""

    _check_message_type(frame, MessageType.REALTIME)
    reader = FieldReader(frame)
    ports = {port: decode_port_reading(reader.required(tag))
             for tag, port in enumerate(PORTS, RealtimeTag.FIRST_PORT)}

    metadata = _decode_metadata(reader, RealtimeTag.METADATA)
    provisional = _decode_metadata(reader, RealtimeTag.PROVISIONAL_METADATA)

    response_marker = None
    marker = reader.unique(TAG_SESSION)
    if marker is not None and len(marker.raw) == 1:
        reader.consume(TAG_SESSION)
        response_marker = marker.raw[0]

    timestamp = None
    stamp = reader.unique(TAG_TIMESTAMP)
    if (stamp is not None and len(stamp.raw) == 5
            and stamp.raw[0] == ValueType.U32):
        reader.consume(TAG_TIMESTAMP)
        timestamp = int.from_bytes(stamp.raw[1:], 'little')

    return RealtimeTelemetry(response_marker=response_marker, ports=ports,
                             metadata=metadata or (),
                             provisional_metadata=provisional,
                             timestamp=timestamp,
                             unknown_fields=reader.unconsumed())


def decode_status_snapshot(frame: Frame) -> StatusSnapshot:
    """Decode a ``0x0A00`` frame: strict port readings, lenient settings."""

    _check_message_type(frame, MessageType.STATUS_SNAPSHOT)
    reader = FieldReader(frame)
    ports = {port: decode_port_reading(reader.required(tag))
             for tag, port in enumerate(PORTS, SnapshotTag.FIRST_PORT)}
    settings = DeviceSettings.from_snapshot(frame)
    return StatusSnapshot(ports, settings, reader.unconsumed())


def decode_version_info(frame: Frame) -> VersionInfo:
    _check_message_type(frame, MessageType.VERSION_INFO)
    reader = FieldReader(frame)
    hardware = decode_string(reader.required(VersionTag.HARDWARE_VERSION))
    software = decode_string(reader.required(VersionTag.SOFTWARE_VERSION))
    product = _optional_string(reader, VersionTag.PRODUCT_CODE)
    mcu = _optional_string(reader, VersionTag.MCU_COMPONENT)
    esp32 = _optional_string(reader, VersionTag.ESP32_COMPONENT)
    return VersionInfo(hardware_version=hardware,
                       software_version=software.removeprefix('v'),
                       product_code=product, mcu_component=mcu,
                       esp32_component=esp32,
                       unknown_fields=reader.unconsumed())


def decode_acknowledgement(frame: Frame) -> Acknowledgement:
    """Decode a 14-byte ``0x0A0A``/``0x0A0B``/``0x0A14`` telemetry ack."""

    if frame.message_type not in TELEMETRY_ACK_TYPES:
        raise MessageError(MessageErrorKind.UNEXPECTED_MESSAGE_TYPE,
                           f'{frame.message_type:#06x} is not an ack')
    if frame.length != ACK_FRAME_LENGTH:
        raise MessageError(MessageErrorKind.INVALID_ACK_LENGTH,
                           f'{frame.length} bytes')
    field = FieldReader(frame).required(TAG_SESSION)
    if len(field.raw) != 1:
        raise MessageError(MessageErrorKind.INVALID_FIELD_LENGTH,
                           f'expected 1, got {len(field.raw)}', field.tag)
    return Acknowledgement(frame.message_type, frame.increment, field.raw[0])


def decode_port_reading(field: Tlv) -> RawPortReading:
    """Decode a port struct; longer mainland fields keep the same first bytes.

    The tail bytes follow the official app's length gates: cable and fast
    charge need more than eight payload bytes, the brand more than nine.
    """

    value = typed_payload(field, ValueType.BYTES, PORT_PAYLOAD_LENGTH)
    millivolts = int.from_bytes(value[1:3], 'little')
    milliamps = int.from_bytes(value[3:5], 'little')
    centiwatts = int.from_bytes(value[5:7], 'little')
    return RawPortReading(value[0], millivolts, milliamps, centiwatts,
                          cable=value[7] if len(value) > 8 else None,
                          fast_charge=value[8] if len(value) > 8 else None,
                          device_brand=value[9] if len(value) > 9 else None)


def typed_payload(field: Tlv, value_type: int, min_length: int) -> bytes:
    """Return the payload after the value-type byte, checking type and length."""

    if not field.raw or field.raw[0] != value_type:
        actual = f'{field.raw[0]:#04x}' if field.raw else 'none'
        raise MessageError(MessageErrorKind.UNEXPECTED_FIELD_TYPE,
                           f'expected {value_type:#04x}, got {actual}',
                           field.tag)
    if len(field.raw) < min_length + 1:
        raise MessageError(MessageErrorKind.INVALID_FIELD_LENGTH,
                           f'expected {min_length + 1}, got {len(field.raw)}',
                           field.tag)
    return field.raw[1:]


def decode_string(field: Tlv) -> str:
    """Decode a string field with or without the 0x00 type byte.

    Trailing NUL bytes are dropped; a printable first byte means the type
    byte is absent.
    """

    raw = field.raw
    if not raw or (raw[0] != ValueType.STRING and raw[0] < 0x20):
        raise MessageError(MessageErrorKind.UNEXPECTED_FIELD_TYPE,
                           'not a string', field.tag)
    if raw[0] == ValueType.STRING:
        raw = raw[1:]
    try:
        return raw.rstrip(b'\x00').decode('utf-8')
    except UnicodeDecodeError:
        raise MessageError(MessageErrorKind.INVALID_UTF8, raw.hex(),
                           field.tag) from None


def _check_message_type(frame: Frame, expected: int) -> None:
    if frame.message_type != expected:
        raise MessageError(MessageErrorKind.UNEXPECTED_MESSAGE_TYPE,
                           f'expected {expected:#06x}, '
                           f'got {frame.message_type:#06x}')


def _decode_metadata(reader: FieldReader, tag: int
                     ) -> tuple[MetadataSlot, ...] | None:
    field = reader.unique(tag)
    if field is None:
        return None
    if (len(field.raw) != 1 + SLOT_LENGTH * len(USB_C_PORTS)
            or field.raw[0] != ValueType.BYTES):
        return None
    reader.consume(tag)
    payload = field.raw[1:]
    return tuple(
        MetadataSlot(port, payload[index * SLOT_LENGTH:(index + 1) * SLOT_LENGTH])
        for index, port in enumerate(USB_C_PORTS))


def _optional_string(reader: FieldReader, tag: int) -> str | None:
    field = reader.optional(tag)
    return decode_string(field) if field is not None else None
