"""Screen picture ("cover") transfer of the A2687, ported from Charker.

Three commands put a picture on the charger's own 240x240 screen: ``0x021F``
names it, ``0x0220`` announces the transfer and ``0x0221`` carries the JPEG
156 bytes at a time. The firmware answers every tenth slice and the last one;
sending past an unanswered slice overruns it. ``E1`` in a state report names
the picture shown (low 16 bits of its id).

The layouts come from a third party's recovery of one capture (Charker cites
``LYJW131/anker-prime-ble``); they are consistent with the capture sizes
(47, 49 and 167 bytes) but not otherwise confirmed. The pictures are written
to the charger's storage and no Bluetooth command erases them.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from dataclasses import dataclass

from ankerprime.model.errors import InvalidParameterError
from ankerprime.protocol.a2687.codes import (
    FIELD_A1, FIELD_A2, FIELD_A3, FIELD_A4, FIELD_A5, FIELD_A6,
    FIELD_A7, FIELD_A8, FIELD_TIMESTAMP, FIELD_URL, OP_COVER_TRANSFER_CHUNK,
    OP_COVER_TRANSFER_START, OP_SET_SCREENSAVER, SESSION_ACTION, SESSION_GROUP)
from ankerprime.protocol.a2687.commands import Encryption, OutgoingMessage
from ankerprime.protocol.a2687.tlv import (
    Payload, TypedValue, encode_fields, typed_bytes,
    typed_text, typed_u8, typed_u16, typed_u32)
from ankerprime.utils import u32le


CHUNK_SIZE = 156
ACKNOWLEDGE_EVERY = 10
SCREEN_SIZE = 240
MAX_CHUNKS = 0xFFFF
CUSTOM_SCREENSAVER = 3
# The only value ever seen in the URL slot; whether firmware checks it is
# unknown.
URL_PLACEHOLDER = 'SmallChargingUrl'
MAX_URL_LENGTH = 254
# The last slice is answered with this once the whole picture is stored.
TRANSFER_COMPLETE = 0x10
# Ids keep a non-zero high half, away from the built-in screensaver types.
MINTED_ID_BASE = 0x0001_0000


@dataclass(init=False)
class CoverPlan:
    """A picture prepared for the transfer: id, CRC-32, slices."""

    _jpeg: bytes
    _picture_id: int
    _crc32: int

    def __init__(self, jpeg: bytes, picture_id: int):
        if not jpeg:
            raise InvalidParameterError('empty picture')
        if chunk_count(len(jpeg)) > MAX_CHUNKS:
            raise InvalidParameterError('picture too large to transfer')
        self._jpeg = jpeg
        self._picture_id = picture_id
        self._crc32 = zlib.crc32(jpeg)

    @property
    def picture_id(self) -> int:
        return self._picture_id

    @property
    def crc32(self) -> int:
        """Both ``0x021F`` and ``0x0220`` carry it; they must agree."""

        return self._crc32

    @property
    def byte_count(self) -> int:
        return len(self._jpeg)

    @property
    def chunk_count(self) -> int:
        return chunk_count(len(self._jpeg))

    @property
    def reported_id(self) -> int:
        """The id as ``E1`` reports it: its low 16 bits."""

        return self._picture_id & 0xFFFF

    def chunks(self) -> Iterator[tuple[int, bytes]]:
        """Index and zero-padded payload of every slice."""

        for index in range(self.chunk_count):
            start = index * CHUNK_SIZE
            payload = self._jpeg[start:start + CHUNK_SIZE]
            yield index, payload.ljust(CHUNK_SIZE, b'\x00')

    def is_checkpoint(self, index: int) -> bool:
        """The firmware answers this slice; the sender must wait for it."""

        return (index == self.chunk_count - 1
                or (index + 1) % ACKNOWLEDGE_EVERY == 0)


def chunk_count(byte_count: int) -> int:
    return (byte_count + CHUNK_SIZE - 1) // CHUNK_SIZE


def mint_picture_id(jpeg: bytes, avoiding: int | None) -> int:
    """Id for a locally pushed picture, as Charker mints it.

    Derived from the pixels, so pushing the same picture again reuses its
    id rather than a second storage slot; never the id shown now, or the
    read-back could not tell a change from no change.
    """

    low = zlib.crc32(jpeg) & 0xFFFF
    if low == avoiding:
        low = (low + 1) & 0xFFFF
    return MINTED_ID_BASE | low


def select_cover(picture_id: int, crc32: int, timestamp: int,
                 url_key: str = URL_PLACEHOLDER) -> OutgoingMessage:
    """``0x021F``: show the picture with this id and CRC-32."""

    if len(url_key.encode('utf-8')) > MAX_URL_LENGTH:
        raise InvalidParameterError('cover URL key too long')
    return _message(OP_SET_SCREENSAVER, [
        (FIELD_A3, typed_u8(CUSTOM_SCREENSAVER)),
        (FIELD_A4, typed_bytes(u32le(picture_id))),
        (FIELD_A5, typed_bytes(u32le(crc32))),
        (FIELD_URL, typed_text(url_key)),
    ], timestamp)


def start_transfer(plan: CoverPlan, timestamp: int) -> OutgoingMessage:
    """``0x0220``: announce the size and slicing of the transfer."""

    return _message(OP_COVER_TRANSFER_START, [
        (FIELD_A2, typed_u8(1)),
        (FIELD_A3, typed_bytes(u32le(plan.picture_id))),
        (FIELD_A4, typed_bytes(u32le(plan.crc32))),
        (FIELD_A5, typed_u32(plan.byte_count)),
        (FIELD_A6, typed_u8(ACKNOWLEDGE_EVERY)),
        (FIELD_A7, typed_u16(CHUNK_SIZE)),
        (FIELD_A8, typed_u16(plan.chunk_count)),
    ], timestamp)


def transfer_chunk(index: int, payload: bytes) -> OutgoingMessage:
    """``0x0221``: one slice; unlike other commands without a timestamp."""

    if len(payload) != CHUNK_SIZE:
        raise InvalidParameterError('slice must be padded to 156 bytes')
    fields = [(FIELD_A1, bytes((SESSION_ACTION,))),
              (FIELD_A2, typed_u16(index)), (FIELD_A3, typed_bytes(payload))]
    return OutgoingMessage(SESSION_GROUP, OP_COVER_TRANSFER_CHUNK,
                           encode_fields(fields), Encryption.SESSION)


def next_index(reply: Payload) -> int | None:
    """The slice the firmware expects next, from a checkpoint's answer."""

    raw = reply.get(FIELD_A2)
    if raw is None:
        return None
    value = TypedValue.decode(raw)
    if value is not None and value.scalar is not None:
        return value.scalar
    if len(raw) == 2:
        return int.from_bytes(raw, 'little')
    return None


def _message(opcode: int, fields: list[tuple[int, bytes]],
             timestamp: int) -> OutgoingMessage:
    """Cover commands carry the timestamp as a typed u32, unlike the rest."""

    all_fields = [(FIELD_A1, bytes((SESSION_ACTION,))), *fields,
                  (FIELD_TIMESTAMP, typed_u32(timestamp))]
    return OutgoingMessage(SESSION_GROUP, opcode, encode_fields(all_fields),
                           Encryption.SESSION)
