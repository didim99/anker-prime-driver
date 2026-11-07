"""A2687 TLV, typed value and payload tests."""

import pytest

from ankerprime.model.errors import ProtocolError
from ankerprime.protocol.a2687.tlv import (
    Payload, TypedValue, decode_fields, encode_fields, typed_bytes, typed_u8)


def test_reply_status_is_split_off() -> None:
    payload = Payload.parse(bytes.fromhex('00a10102a2022901'))
    assert payload.status == 0 and payload.ok
    assert payload.get(0xA2) == b'\x29\x01'
    assert [field.tag for field in payload.fields] == [0xA1, 0xA2]


def test_request_has_no_status() -> None:
    payload = Payload.parse(bytes.fromhex('a104ef79b569'))
    assert payload.status is None and payload.ok
    assert payload.get(0xA1) == bytes.fromhex('ef79b569')


def test_refusal_keeps_its_status() -> None:
    payload = Payload.parse(bytes.fromhex('11a10131'))
    assert payload.status == 0x11
    assert not payload.ok
    assert payload.get(0xA1) == b'\x31'
    bare = Payload.parse(b'\x04')
    assert bare.status == 0x04 and not bare.ok
    assert Payload.parse(b'').fields == ()


def test_unparsable_payload_raises() -> None:
    with pytest.raises(ProtocolError):
        Payload.parse(bytes.fromhex('a105ffff'))
    with pytest.raises(ProtocolError):
        decode_fields(bytes.fromhex('a1'))


def test_typed_values() -> None:
    u8 = TypedValue.decode(typed_u8(0x50))
    assert u8 is not None and u8.scalar == 0x50 and u8.block is None
    u16 = TypedValue.decode(bytes.fromhex('022901'))
    assert u16 is not None and u16.scalar == 0x0129
    u32 = TypedValue.decode(bytes.fromhex('0300f15365'))
    assert u32 is not None and u32.scalar == 1700000000
    block = TypedValue.decode(typed_bytes(b'\x01\x02'))
    assert block is not None and block.block == b'\x01\x02'
    assert block.scalar is None
    text = TypedValue.decode(b'\x00Prime\x00..')
    assert text is not None and text.text == 'Prime'
    short = TypedValue.decode(b'\x03\x01')
    assert short is not None and short.scalar is None
    assert TypedValue.decode(b'') is None


def test_encode_round_trip() -> None:
    fields = [(0xA1, b'\x21'), (0xA2, typed_u8(1)), (0xFE, b'\x00' * 4)]
    encoded = encode_fields(fields)
    assert encoded.hex() == 'a10121a2020101fe0400000000'
    assert [(field.tag, field.value)
            for field in decode_fields(encoded)] == fields
    with pytest.raises(ProtocolError):
        encode_fields([(0xA1, b'\x00' * 256)])
