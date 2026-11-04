"""FF09 frame codec of the A2345.

Frame layout::

    FF 09                 marker
    uint16 LE length      whole frame including the checksum
    version speaker group three-byte pattern, version is always 3
    uint16 BE type        message type
    [increment]           present when the next byte is below A0
    TLVs                  tag | uint8 length | value
    checksum              XOR of all previous bytes

Decoding is strict: malformed frames are rejected, never repaired.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import reduce

from ankerprime.model.errors import ProtocolError
from ankerprime.utils import u32le


HEADER = b'\xff\x09'
PROTOCOL_VERSION = 0x03
MIN_FRAME_LENGTH = 10
MAX_FRAME_LENGTH = 2048

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


class FrameErrorKind(StrEnum):
    """Reason a frame was rejected."""

    TOO_SHORT = 'too_short'
    TOO_LARGE = 'too_large'
    BAD_HEADER = 'bad_header'
    INVALID_LENGTH = 'invalid_length'
    LENGTH_MISMATCH = 'length_mismatch'
    UNSUPPORTED_VERSION = 'unsupported_version'
    BAD_CHECKSUM = 'bad_checksum'
    TRUNCATED_FIELD = 'truncated_field'


class FrameError(ProtocolError):
    """A byte sequence is not a valid FF09 frame."""

    _kind: FrameErrorKind

    def __init__(self, kind: FrameErrorKind, detail: str):
        super().__init__(f'invalid FF09 frame ({kind}): {detail}')
        self._kind = kind

    @property
    def kind(self) -> FrameErrorKind:
        return self._kind


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


class FrameReassembler:
    """Splits a byte stream of split or coalesced chunks into valid frames.

    Garbage and malformed candidates are dropped and counted; a plausible but
    corrupt length never stalls the stream once a later complete frame
    validates.
    """

    _buffer: bytearray

    _dropped_bytes: int
    _rejected_frames: int
    _last_rejection: FrameError | None

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._dropped_bytes = 0
        self._rejected_frames = 0
        self._last_rejection = None

    @property
    def dropped_bytes(self) -> int:
        return self._dropped_bytes

    @property
    def rejected_frames(self) -> int:
        return self._rejected_frames

    @property
    def last_rejection(self) -> FrameError | None:
        return self._last_rejection

    def reset(self) -> None:
        """Discard buffered bytes; counters are kept."""

        self._buffer.clear()

    def feed(self, chunk: bytes) -> list[Frame]:
        """Append a chunk and return every frame it completes."""

        self._buffer += chunk
        frames = []
        while True:
            offset = self._next_header_offset(0)
            if offset is None:
                self._keep_possible_header_prefix()
                break
            self._drop(offset)
            if len(self._buffer) < 4:
                break

            length = int.from_bytes(self._buffer[2:4], 'little')
            if not MIN_FRAME_LENGTH <= length <= MAX_FRAME_LENGTH:
                self._reject(FrameError(FrameErrorKind.INVALID_LENGTH,
                                        f'declared length {length}'))
                continue

            if len(self._buffer) < length:
                # A false length must not wait forever for bytes that belong
                # to a later frame; resync only on a fully valid later frame.
                recovery = self._next_valid_frame_offset(2)
                if recovery is None:
                    break
                error = FrameError(FrameErrorKind.LENGTH_MISMATCH,
                                   f'declared {length}, got {recovery}')
                self._reject(error, recovery)
                continue

            try:
                frames.append(decode_frame(bytes(self._buffer[:length])))
            except FrameError as error:
                # Drop only the marker: a corrupt length may span a valid frame.
                self._reject(error)
                continue
            del self._buffer[:length]
        return frames

    def _next_header_offset(self, start: int) -> int | None:
        offset = self._buffer.find(HEADER, start)
        return offset if offset >= 0 else None

    def _next_valid_frame_offset(self, start: int) -> int | None:
        offset = self._next_header_offset(start)
        while offset is not None:
            header = self._buffer[offset + 2:offset + 4]
            length = int.from_bytes(header, 'little')
            end = offset + length
            if (offset + 4 <= len(self._buffer)
                    and MIN_FRAME_LENGTH <= length <= MAX_FRAME_LENGTH
                    and end <= len(self._buffer)
                    and _is_valid_frame(bytes(self._buffer[offset:end]))):
                return offset
            offset = self._next_header_offset(offset + 1)
        return None

    def _keep_possible_header_prefix(self) -> None:
        keep = 1 if self._buffer.endswith(HEADER[:1]) else 0
        self._drop(len(self._buffer) - keep)

    def _reject(self, error: FrameError, skip: int = len(HEADER)) -> None:
        self._rejected_frames += 1
        self._last_rejection = error
        self._drop(skip)

    def _drop(self, count: int) -> None:
        self._dropped_bytes += count
        del self._buffer[:count]


def checksum(data: Iterable[int]) -> int:
    """XOR of all bytes."""

    return reduce(lambda acc, byte: acc ^ byte, data, 0)


def decode_frame(data: bytes) -> Frame:
    """Decode one complete frame; raises ``FrameError`` on any defect."""

    if len(data) < MIN_FRAME_LENGTH:
        raise FrameError(FrameErrorKind.TOO_SHORT, f'{len(data)} bytes')
    if len(data) > MAX_FRAME_LENGTH:
        raise FrameError(FrameErrorKind.TOO_LARGE, f'{len(data)} bytes')
    if data[:2] != HEADER:
        raise FrameError(FrameErrorKind.BAD_HEADER, data[:2].hex())

    length = int.from_bytes(data[2:4], 'little')
    if not MIN_FRAME_LENGTH <= length <= MAX_FRAME_LENGTH:
        raise FrameError(FrameErrorKind.INVALID_LENGTH, f'declared {length}')
    if length != len(data):
        raise FrameError(FrameErrorKind.LENGTH_MISMATCH,
                         f'declared {length}, got {len(data)}')
    if data[4] != PROTOCOL_VERSION:
        raise FrameError(FrameErrorKind.UNSUPPORTED_VERSION, f'{data[4]}')
    computed = checksum(data[:-1])
    if computed != data[-1]:
        raise FrameError(FrameErrorKind.BAD_CHECKSUM,
                         f'encoded {data[-1]:#04x}, computed {computed:#04x}')

    end = len(data) - 1
    offset = 9
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
                 increment=increment, fields=fields, length=length)


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
    body[2:4] = (len(body) + 1).to_bytes(2, 'little')
    body.append(checksum(body))
    return bytes(body)


def _is_valid_frame(data: bytes) -> bool:
    try:
        decode_frame(data)
    except FrameError:
        return False
    return True


def _value_type(length: int) -> int:
    return VALUE_TYPES_BY_LENGTH.get(length, VALUE_TYPE_BYTES)
