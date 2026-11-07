"""Plaintext messages the app sends: negotiation and session commands.

Session commands carry ``A1 = 21``, their fields, and a trailing ``FE`` field
with the bare four-byte epoch (not a typed u32). Values are typed u8 unless
noted. Encryption happens in the session layer.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ankerprime.protocol.a2687.codes import (
    NEGOTIATION_GROUP, SESSION_ACTION, SESSION_GROUP, FieldId, Opcode)
from ankerprime.protocol.a2687.tlv import (
    encode_fields, typed_bytes, typed_u8, typed_u32)
from ankerprime.utils import u32le


CUSTOM_MODE_CODE = 2

# The enable block the official app sends with the realtime trigger.
REALTIME_ENABLE = bytes.fromhex('010003150101000000')


class Encryption(StrEnum):
    """Key schedule a message is sealed with."""

    NEGOTIATION = 'negotiation'
    SESSION = 'session'


@dataclass(init=False)
class OutgoingMessage:
    """A message still in plaintext."""

    _group: int
    _opcode: int
    _plaintext: bytes
    _encryption: Encryption

    def __init__(self, group: int, opcode: int, plaintext: bytes,
                 encryption: Encryption):
        self._group = group
        self._opcode = opcode
        self._plaintext = plaintext
        self._encryption = encryption

    @property
    def group(self) -> int:
        return self._group

    @property
    def opcode(self) -> int:
        return self._opcode

    @property
    def plaintext(self) -> bytes:
        return self._plaintext

    @property
    def encryption(self) -> Encryption:
        return self._encryption


def negotiation(opcode: int, fields: Iterable[tuple[int, bytes]],
                encryption: Encryption) -> OutgoingMessage:
    return OutgoingMessage(NEGOTIATION_GROUP, opcode, encode_fields(fields),
                           encryption)


def session(opcode: int, fields: Iterable[tuple[int, bytes]],
            timestamp: int, *, typed_timestamp: bool = False
            ) -> OutgoingMessage:
    """A session command; the screen picture commands type the timestamp."""

    stamp = typed_u32(timestamp) if typed_timestamp else u32le(timestamp)
    all_fields = [(FieldId.A1, bytes((SESSION_ACTION,))), *fields,
                  (FieldId.TIMESTAMP, stamp)]
    return OutgoingMessage(SESSION_GROUP, opcode, encode_fields(all_fields),
                           Encryption.SESSION)


def read_all(timestamp: int) -> OutgoingMessage:
    """0x0200: full state snapshot."""

    return session(Opcode.READ_ALL, (), timestamp)


def realtime_probe(country: str, owner_id: str | None,
                   timestamp: int) -> OutgoingMessage:
    """0x020A, the "bind success" getter; A3 carries the owning account."""

    fields = [(FieldId.A2, typed_bytes(country.encode()))]
    if owner_id:
        fields.append((FieldId.A3, typed_bytes(owner_id.encode())))
    fields.append((FieldId.A5, typed_u8(1)))
    return session(Opcode.BIND_SUCCESS, fields, timestamp)


def realtime_trigger(timestamp: int) -> OutgoingMessage:
    """0x020B with the enable block: arms the ~1 Hz report stream."""

    return session(Opcode.REALTIME_TRIGGER,
                   [(FieldId.A2, typed_bytes(REALTIME_ENABLE))], timestamp)


def set_port_output(port: int, on: bool, timestamp: int) -> OutgoingMessage:
    """0x0207: port index 0..2 on or off."""

    return session(Opcode.PORT_OUTPUT, [(FieldId.A2, typed_u8(port)),
                                        (FieldId.A3, typed_u8(int(on)))],
                   timestamp)


def set_port_timer(port: int, seconds: int,
                   timestamp: int) -> OutgoingMessage:
    """0x0209: switch the port off after the seconds; 0 cancels."""

    return session(Opcode.PORT_TIMER,
                   [(FieldId.A2, typed_u8(port)),
                    (FieldId.A3, typed_bytes(u32le(seconds)))], timestamp)


def set_charging_mode(code: int, timestamp: int) -> OutgoingMessage:
    """0x0206: 0 AI, 1 standard, 2 custom (custom also needs power limits)."""

    return session(Opcode.CHARGING_MODE, [(FieldId.A2, typed_u8(code))], timestamp)


def set_custom_charging_mode(number: int, auto_exit: bool,
                             watts: Sequence[int],
                             timestamp: int) -> OutgoingMessage:
    """0x0206 mode 2 with the C1-C3 limits: number, auto exit, watts.

    Firmware with its own protocol management takes no protocol list, so
    none is sent (as Charker).
    """

    block = bytes((number, int(auto_exit), *watts))
    return session(Opcode.CHARGING_MODE,
                   [(FieldId.A2, typed_u8(CUSTOM_MODE_CODE)),
                    (FieldId.A3, typed_bytes(block))], timestamp)


def set_setting(opcode: int, value: int, timestamp: int) -> OutgoingMessage:
    """A single typed-u8 setting: language, timeout, brightness, ..."""

    return session(opcode, [(FieldId.A2, typed_u8(value))], timestamp)
