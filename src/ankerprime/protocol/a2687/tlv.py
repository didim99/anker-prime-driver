"""TLV fields, typed values and payloads of the A2687 protocol.

A field is ``id | uint8 length | value``. Most values start with a type byte:
00 text, 01 u8, 02 u16 LE, 03 u32 LE, 04 byte block. Device replies lead the
field list with a status byte (00 = OK); requests have none.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from ankerprime.model.errors import ProtocolError


TYPE_TEXT = 0x00
TYPE_U8 = 0x01
TYPE_U16 = 0x02
TYPE_U32 = 0x03
TYPE_BYTES = 0x04
SCALAR_LENGTHS = {TYPE_U8: 1, TYPE_U16: 2, TYPE_U32: 4}

STATUS_OK = 0x00


@dataclass(init=False)
class Tlv:
    """One field in wire order."""

    _tag: int
    _value: bytes

    def __init__(self, tag: int, value: bytes):
        self._tag = tag
        self._value = value

    @property
    def tag(self) -> int:
        return self._tag

    @property
    def value(self) -> bytes:
        return self._value


@dataclass(init=False)
class TypedValue:
    """A value with its leading type byte split off."""

    _type_code: int
    _body: bytes

    def __init__(self, type_code: int, body: bytes):
        self._type_code = type_code
        self._body = body

    @property
    def type_code(self) -> int:
        return self._type_code

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def scalar(self) -> int | None:
        """The number of a u8/u16/u32 value long enough for its type."""

        length = SCALAR_LENGTHS.get(self._type_code)
        if length is None or len(self._body) < length:
            return None
        return int.from_bytes(self._body[:length], 'little')

    @property
    def block(self) -> bytes | None:
        """The bytes of a byte-block value."""

        return self._body if self._type_code == TYPE_BYTES else None

    @property
    def text(self) -> str | None:
        """Text without NUL and trailing dot padding."""

        if self._type_code != TYPE_TEXT:
            return None
        return self._body.decode('utf-8', 'replace').replace(
            '\x00', '').rstrip('.')

    @classmethod
    def decode(cls, raw: bytes) -> Self | None:
        if not raw:
            return None
        return cls(raw[0], raw[1:])


@dataclass(init=False)
class Payload:
    """A plaintext payload: optional status byte and fields."""

    _status: int | None
    _fields: tuple[Tlv, ...]

    def __init__(self, status: int | None, fields: Iterable[Tlv]):
        self._status = status
        self._fields = tuple(fields)

    @property
    def status(self) -> int | None:
        return self._status

    @property
    def fields(self) -> tuple[Tlv, ...]:
        return self._fields

    @property
    def ok(self) -> bool:
        return self._status is None or self._status == STATUS_OK

    def get(self, tag: int) -> bytes | None:
        """Value of the first field with the tag."""

        for field in self._fields:
            if field.tag == tag:
                return field.value
        return None

    def typed(self, tag: int) -> TypedValue | None:
        value = self.get(tag)
        return None if value is None else TypedValue.decode(value)

    @classmethod
    def parse(cls, data: bytes) -> Self:
        """Split off a status byte when the rest parses as fields.

        Replies lead with 00; requests have no status; a refusal is a
        non-zero status, optionally followed by fields.
        """

        if not data:
            return cls(None, ())
        if data[0] == STATUS_OK:
            fields = _try_decode(data, 1)
            if fields is not None:
                return cls(STATUS_OK, fields)
        fields = _try_decode(data, 0)
        if fields is not None:
            return cls(None, fields)
        fields = _try_decode(data, 1)
        if fields is not None:
            return cls(data[0], fields)
        raise ProtocolError(f'unparsable A2687 payload ({len(data)} bytes)')


def decode_fields(data: bytes, offset: int = 0) -> list[Tlv]:
    """Strict parse; a truncated field raises ``ProtocolError``."""

    fields = []
    while offset < len(data):
        if offset + 2 > len(data):
            raise ProtocolError(f'truncated A2687 field at {offset}')
        end = offset + 2 + data[offset + 1]
        if end > len(data):
            raise ProtocolError(f'truncated A2687 field at {offset}')
        fields.append(Tlv(data[offset], bytes(data[offset + 2:end])))
        offset = end
    return fields


def encode_fields(fields: Iterable[tuple[int, bytes]]) -> bytes:
    body = bytearray()
    for tag, value in fields:
        if len(value) > 0xFF:
            raise ProtocolError(f'A2687 field {tag:#04x} too long')
        body += bytes((tag, len(value))) + value
    return bytes(body)


def typed_u8(value: int) -> bytes:
    return bytes((TYPE_U8, value & 0xFF))


def typed_u16(value: int) -> bytes:
    return bytes((TYPE_U16,)) + (value & 0xFFFF).to_bytes(2, 'little')


def typed_u32(value: int) -> bytes:
    return bytes((TYPE_U32,)) + (value & 0xFFFFFFFF).to_bytes(4, 'little')


def typed_bytes(value: bytes) -> bytes:
    return bytes((TYPE_BYTES,)) + value


def typed_text(value: str) -> bytes:
    return bytes((TYPE_TEXT,)) + value.encode('utf-8')


def _try_decode(data: bytes, offset: int) -> list[Tlv] | None:
    try:
        return decode_fields(data, offset)
    except ProtocolError:
        return None
