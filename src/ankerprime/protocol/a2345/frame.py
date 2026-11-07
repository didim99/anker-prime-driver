"""FF09 frame codec of the A2345.

Payload layout, inside the shared envelope (``ankerprime.protocol.ff09``)::

    [increment]           present when the next byte is below A0
    TLVs                  tag | uint8 length | value

Decoding is strict: malformed frames are rejected, never repaired.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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

VALUE_TYPE_U8 = 0x01
VALUE_TYPE_U16 = 0x02
VALUE_TYPE_U32 = 0x03
VALUE_TYPE_BYTES = 0x04
VALUE_TYPES_BY_LENGTH = {1: VALUE_TYPE_U8, 2: VALUE_TYPE_U16, 4: VALUE_TYPE_U32}
LONG_FIELD_THRESHOLD = 0x78


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
    body += bytes((TAG_TIMESTAMP, 5, VALUE_TYPE_U32))
    body += u32le(timestamp)
    return seal_frame(body)


def _value_type(length: int) -> int:
    return VALUE_TYPES_BY_LENGTH.get(length, VALUE_TYPE_BYTES)
