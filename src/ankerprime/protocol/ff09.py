"""FF09 framing shared by the A2345 and A2687 protocols.

Envelope::

    FF 09                 marker
    uint16 LE length      whole frame including the checksum
    version speaker group three-byte pattern, version is always 3
    uint16 BE command     message type / opcode with flag bits
    payload               protocol specific
    checksum              XOR of all previous bytes

Each protocol decodes its own payload; this module validates the envelope and
splits a byte stream into frames.
"""

from collections.abc import Callable, Iterable
from enum import StrEnum
from functools import reduce
from typing import Generic, TypeVar

from ankerprime.model.errors import ProtocolError


HEADER = b'\xff\x09'
PROTOCOL_VERSION = 0x03
MIN_FRAME_LENGTH = 10
MAX_FRAME_LENGTH = 2048
PAYLOAD_OFFSET = 9

FrameT = TypeVar('FrameT')


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


def checksum(data: Iterable[int]) -> int:
    """XOR of all bytes."""

    return reduce(lambda acc, byte: acc ^ byte, data, 0)


def validate_envelope(data: bytes) -> None:
    """Raise ``FrameError`` unless the bytes are one well-formed frame."""

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


def seal_frame(body: bytearray) -> bytes:
    """Fill in the length of a frame body and append its checksum."""

    body[2:4] = (len(body) + 1).to_bytes(2, 'little')
    body.append(checksum(body))
    return bytes(body)


class FrameReassembler(Generic[FrameT]):
    """Splits a byte stream of split or coalesced chunks into valid frames.

    Garbage and malformed candidates are dropped and counted; a plausible but
    corrupt length never stalls the stream once a later complete frame
    validates.
    """

    _decode: Callable[[bytes], FrameT]
    _buffer: bytearray

    _dropped_bytes: int
    _rejected_frames: int
    _last_rejection: FrameError | None

    def __init__(self, decode: Callable[[bytes], FrameT]):
        self._decode = decode
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

    def feed(self, chunk: bytes) -> list[FrameT]:
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
                frames.append(self._decode(bytes(self._buffer[:length])))
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
                    and self._is_valid(bytes(self._buffer[offset:end]))):
                return offset
            offset = self._next_header_offset(offset + 1)
        return None

    def _is_valid(self, data: bytes) -> bool:
        try:
            self._decode(data)
        except FrameError:
            return False
        return True

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
