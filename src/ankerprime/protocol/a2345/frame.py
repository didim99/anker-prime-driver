"""FF09 frame codec of the A2345.

Payload layout, inside the shared envelope (``ankerprime.protocol.ff09``)::

    [increment]           present when the next byte is below A0
    TLVs                  tag | uint8 length | value

Decoding is strict: malformed frames are rejected, never repaired.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum

from ankerprime.model.errors import ProtocolError
from ankerprime.protocol import ff09
from ankerprime.protocol.ff09 import (
    HEADER, PAYLOAD_OFFSET, PROTOCOL_VERSION, FrameError,
    FrameErrorKind, seal_frame, validate_envelope)
from ankerprime.utils import u32le


REQUEST_SPEAKER = 0x00
REQUEST_GROUP = 0x0F
CLOUD_SESSION = 0x22

TAG_SESSION = 0xA1
TAG_TIMESTAMP = 0xFE
MIN_TAG = 0xA0

LONG_FIELD_THRESHOLD = 0x78


class MessageType(IntEnum):
    """Message types of the A2345 (the frame's command field)."""

    # Requests (client to charger).
    STATUS_SNAPSHOT_REQUEST = 0x0200
    THEME_STATUS_REQUEST = 0x0202
    SCREEN_TIMEOUT = 0x0203
    BRIGHTNESS = 0x0204
    CLOCK_THEME = 0x0205
    CHARGING_MODE = 0x0206
    OUTPUT_SWITCH = 0x0207
    SCHEDULE = 0x0208
    COUNTDOWN = 0x0209
    REALTIME_TRIGGER = 0x020B
    PORT_PRIORITY = 0x020C
    KNOB_DIRECTION = 0x020E
    HOLIDAY_THEME = 0x020F
    HOUR_FORMAT = 0x0210
    EXIT_CUSTOM_MODE = 0x0211
    CLOCK_SCHEDULE = 0x0213
    MAX_COMPATIBILITY = 0x0214
    UFCS = 0x0215
    IMAGE_TIME_DISPLAY = 0x0222
    DEVICE_IDENTIFICATION = 0x0223

    # Change reports and telemetry (charger to client).
    FIRMWARE_VERSION_CHANGE = 0x0301
    OUTPUT_CHANGE = 0x0302
    REALTIME = 0x0303
    ERROR_CODE_CHANGE = 0x0304
    COUNTDOWN_CHANGE = 0x0307
    SCHEDULE_CHANGE = 0x0308
    SCREEN_TIMEOUT_CHANGE = 0x0309
    BRIGHTNESS_CHANGE = 0x030A
    CLOCK_CHANGE = 0x030B
    CHARGING_MODE_CHANGE = 0x030D
    PRIORITY_CHANGE = 0x030F
    KNOB_DIRECTION_CHANGE = 0x0310
    THEME_DOWNLOAD_CHANGE = 0x0311
    VERSION_INFO = 0x0830

    # Snapshots, acknowledgements and their replies.
    STATUS_SNAPSHOT = 0x0A00
    THEME_STATUS = 0x0A02
    OUTPUT_SWITCH_ACK = 0x0A07
    SCHEDULE_ACK = 0x0A08
    COUNTDOWN_ACK = 0x0A09
    REALTIME_ACK = 0x0A0B


class ValueType(IntEnum):
    """Leading type byte of a field value."""

    STRING = 0x00
    U8 = 0x01
    U16 = 0x02
    U32 = 0x03
    BYTES = 0x04


VALUE_TYPES_BY_LENGTH: dict[int, int] = {
    1: ValueType.U8, 2: ValueType.U16, 4: ValueType.U32}


class Message:
    """Base class of every decoded A2345 message."""


@dataclass(init=False)
class Tlv:
    """One field in wire order; ``raw`` keeps any value-type prefix."""

    _tag: int
    _raw: bytes

    def __init__(self, tag: int, raw: bytes):
        self._tag = tag
        self._raw = raw

    @property
    def tag(self) -> int:
        return self._tag

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def value_type(self) -> int | None:
        """Leading value-type byte; single-byte fields have none."""

        return self._raw[0] if len(self._raw) > 1 else None


@dataclass(init=False)
class Frame:
    """Decoded FF09 frame."""

    _version: int
    _speaker: int
    _group: int
    _message_type: int
    _increment: int | None
    _fields: tuple[Tlv, ...]
    _length: int

    def __init__(self, *, version: int, speaker: int, group: int,
                 message_type: int, increment: int | None,
                 fields: Iterable[Tlv], length: int):
        self._version = version
        self._speaker = speaker
        self._group = group
        self._message_type = message_type
        self._increment = increment
        self._fields = tuple(fields)
        self._length = length

    @property
    def version(self) -> int:
        return self._version

    @property
    def speaker(self) -> int:
        return self._speaker

    @property
    def group(self) -> int:
        return self._group

    @property
    def message_type(self) -> int:
        return self._message_type

    @property
    def increment(self) -> int | None:
        return self._increment

    @property
    def fields(self) -> tuple[Tlv, ...]:
        return self._fields

    @property
    def length(self) -> int:
        """Encoded frame length in bytes."""

        return self._length

    def field(self, tag: int) -> Tlv | None:
        """Return the first field with the tag."""

        for field in self._fields:
            if field.tag == tag:
                return field
        return None


class FrameReassembler(ff09.FrameReassembler[Frame]):
    """Reassembler producing A2345 frames."""

    def __init__(self) -> None:
        super().__init__(decode_frame)


def decode_frame(data: bytes) -> Frame:
    """Decode one complete frame; raises ``FrameError`` on any defect."""

    validate_envelope(data)
    end = len(data) - 1
    offset = PAYLOAD_OFFSET
    increment = None
    if offset < end and data[offset] < MIN_TAG:
        increment = data[offset]
        offset += 1

    fields = []
    while offset < end:
        if offset + 2 > end or offset + 2 + data[offset + 1] > end:
            raise FrameError(FrameErrorKind.TRUNCATED_FIELD, f'at {offset}')
        value_end = offset + 2 + data[offset + 1]
        fields.append(Tlv(data[offset], data[offset + 2:value_end]))
        offset = value_end

    return Frame(version=data[4], speaker=data[5], group=data[6],
                 message_type=int.from_bytes(data[7:9], 'big'),
                 increment=increment, fields=fields, length=len(data))


def encode_request(message_type: int, fields: Mapping[int, bytes], *,
                   timestamp: int, session: int = CLOUD_SESSION,
                   allow_long_fields: bool = False) -> bytes:
    """Encode a request frame the way the official app does.

    Fields are sorted by tag and typed by value length; the session field
    leads and the epoch timestamp field trails. Long fields (only used for
    picture URLs) get a two-byte little-endian length when allowed.
    """

    body = bytearray(HEADER + b'\x00\x00')
    body += bytes((PROTOCOL_VERSION, REQUEST_SPEAKER, REQUEST_GROUP))
    body += message_type.to_bytes(2, 'big')
    body += bytes((TAG_SESSION, 1, session))
    for tag in sorted(fields):
        value = fields[tag]
        length = len(value) + 1
        body.append(tag)
        if allow_long_fields and length > LONG_FIELD_THRESHOLD:
            body += length.to_bytes(2, 'little')
        elif length <= 0xFF:
            body.append(length)
        else:
            raise ProtocolError(f'field {tag:#04x} too long: {len(value)}')
        body.append(_value_type(len(value)))
        body += value
    body += bytes((TAG_TIMESTAMP, 5, ValueType.U32))
    body += u32le(timestamp)
    return seal_frame(body)


def _value_type(length: int) -> int:
    return VALUE_TYPES_BY_LENGTH.get(length, ValueType.BYTES)
