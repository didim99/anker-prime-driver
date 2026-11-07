"""Field access over one A2345 frame, and the error a bad message raises."""

from enum import StrEnum

from ankerprime.model.errors import ProtocolError
from ankerprime.protocol.a2345.frame import Frame, Tlv


class MessageErrorKind(StrEnum):
    """Reason a message could not be decoded."""

    UNEXPECTED_MESSAGE_TYPE = 'unexpected_message_type'
    MISSING_FIELD = 'missing_field'
    DUPLICATE_FIELD = 'duplicate_field'
    INVALID_FIELD_LENGTH = 'invalid_field_length'
    UNEXPECTED_FIELD_TYPE = 'unexpected_field_type'
    INVALID_UTF8 = 'invalid_utf8'
    INVALID_ACK_LENGTH = 'invalid_ack_length'


class MessageError(ProtocolError):
    """A valid frame does not carry the expected message."""

    _kind: MessageErrorKind
    _tag: int | None

    def __init__(self, kind: MessageErrorKind, detail: str,
                 tag: int | None = None):
        where = f' in field {tag:#04x}' if tag is not None else ''
        super().__init__(f'invalid A2345 message ({kind}){where}: {detail}')
        self._kind = kind
        self._tag = tag

    @property
    def kind(self) -> MessageErrorKind:
        return self._kind

    @property
    def tag(self) -> int | None:
        return self._tag


class FieldReader:
    """Field lookup over one frame.

    Strict access (``required``, ``optional``) remembers which fields were
    consumed, for telemetry decoders that keep the rest as unknown fields.
    Lenient access (``value`` and the typed readers) reads the way the
    official app does: the first field with a tag wins and its value-type
    byte is dropped whatever it is.
    """

    _frame: Frame
    _consumed: set[int]

    def __init__(self, frame: Frame):
        self._frame = frame
        self._consumed = set()

    def required(self, tag: int) -> Tlv:
        """Return the only field with the tag; raises when absent or repeated."""

        field = self.optional(tag)
        if field is None:
            raise MessageError(MessageErrorKind.MISSING_FIELD, 'absent', tag)
        return field

    def optional(self, tag: int) -> Tlv | None:
        """Return the only field with the tag or None; raises when repeated."""

        indexes = self._indexes(tag)
        if len(indexes) > 1:
            raise MessageError(MessageErrorKind.DUPLICATE_FIELD,
                               f'{len(indexes)} occurrences', tag)
        if not indexes:
            return None
        self._consumed.add(indexes[0])
        return self._frame.fields[indexes[0]]

    def unique(self, tag: int) -> Tlv | None:
        """Return the only field with the tag, or None when absent or repeated.

        The field is not consumed; call ``consume`` once it decoded.
        """

        indexes = self._indexes(tag)
        return self._frame.fields[indexes[0]] if len(indexes) == 1 else None

    def consume(self, tag: int) -> None:
        """Mark every field with the tag as interpreted."""

        self._consumed.update(self._indexes(tag))

    def unconsumed(self) -> list[Tlv]:
        """Fields not interpreted, in wire order."""

        return [field for index, field in enumerate(self._frame.fields)
                if index not in self._consumed]

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

    def _indexes(self, tag: int) -> list[int]:
        return [index for index, field in enumerate(self._frame.fields)
                if field.tag == tag]
