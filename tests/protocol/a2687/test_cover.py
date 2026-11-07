"""A2687 screen picture commands and custom mode, Charker's vectors."""

import zlib

import pytest

from ankerprime.model import InvalidParameterError
from ankerprime.protocol.a2687 import commands, cover
from ankerprime.protocol.a2687.tlv import Payload, Tlv, decode_fields


EPOCH = 0x1234_5678


def fields(plaintext: bytes) -> dict[int, bytes]:
    return {field.tag: field.value for field in decode_fields(plaintext)}


def test_custom_mode_vector() -> None:
    message = commands.set_custom_charging_mode(1, True, [100, 40, 20],
                                                1_700_000_000)
    assert message.opcode == 0x0206
    assert message.plaintext.hex() == ('a10121a2020102a306040101642814fe04'
                                       '00f15365')


def test_select_is_47_bytes() -> None:
    message = cover.select_cover(24551, 0xDEADBEEF, EPOCH)
    assert message.opcode == 0x021F
    assert len(message.plaintext) == 47
    values = fields(message.plaintext)
    assert values[0xA3] == b'\x01\x03'
    assert values[0xA4] == b'\x04' + (24551).to_bytes(4, 'little')
    assert values[0xA5] == b'\x04\xef\xbe\xad\xde'
    assert values[0xFD] == b'\x00SmallChargingUrl'
    # Cover commands carry the timestamp as a typed u32.
    assert values[0xFE] == bytes((0x03, 0x78, 0x56, 0x34, 0x12))


def test_select_refuses_a_long_url_key() -> None:
    with pytest.raises(InvalidParameterError):
        cover.select_cover(1, 2, EPOCH, url_key='x' * 255)


def test_start_is_49_bytes() -> None:
    jpeg = bytes(range(250)) * 4
    plan = cover.CoverPlan(jpeg, 0x5FE7)
    message = cover.start_transfer(plan, EPOCH)
    assert message.opcode == 0x0220
    assert len(message.plaintext) == 49
    values = fields(message.plaintext)
    assert values[0xA2] == b'\x01\x01'
    assert values[0xA3] == bytes((0x04, 0xE7, 0x5F, 0x00, 0x00))
    assert values[0xA4] == b'\x04' + zlib.crc32(jpeg).to_bytes(4, 'little')
    assert values[0xA5] == bytes((0x03, 0xE8, 0x03, 0x00, 0x00))
    assert values[0xA6] == b'\x01\x0a'
    assert values[0xA7] == bytes((0x02, 0x9C, 0x00))
    assert values[0xA8] == bytes((0x02, 7, 0x00))


def test_chunk_is_167_bytes_without_timestamp() -> None:
    payload = bytearray(cover.CHUNK_SIZE)
    payload[0] = 0xFF
    message = cover.transfer_chunk(258, bytes(payload))
    assert message.opcode == 0x0221
    assert len(message.plaintext) == 167
    values = fields(message.plaintext)
    assert values[0xA2] == bytes((0x02, 0x02, 0x01))
    assert len(values[0xA3]) == 157
    assert 0xFE not in values
    with pytest.raises(InvalidParameterError):
        cover.transfer_chunk(0, b'short')


def test_plan_slices_and_checkpoints() -> None:
    jpeg = b'\xff\xd8\xff' + bytes(range(256)) * 10
    plan = cover.CoverPlan(jpeg, 0x10042)
    assert plan.byte_count == len(jpeg)
    assert plan.chunk_count == 17
    assert plan.reported_id == 0x0042
    slices = list(plan.chunks())
    assert [index for index, _ in slices] == list(range(17))
    assert all(len(payload) == cover.CHUNK_SIZE for _, payload in slices)
    assert b''.join(payload for _, payload in slices)[:len(jpeg)] == jpeg
    assert slices[-1][1].endswith(b'\x00' * 10)
    checkpoints = [index for index in range(17) if plan.is_checkpoint(index)]
    assert checkpoints == [9, 16]
    with pytest.raises(InvalidParameterError):
        cover.CoverPlan(b'', 1)


def test_minted_id_follows_the_pixels_and_avoids_the_shown_one() -> None:
    jpeg = b'\xff\xd8\xff picture'
    low = zlib.crc32(jpeg) & 0xFFFF
    assert cover.mint_picture_id(jpeg, None) == 0x10000 | low
    assert cover.mint_picture_id(jpeg, low) == 0x10000 | ((low + 1) & 0xFFFF)


@pytest.mark.parametrize('raw, expected', [
    (bytes((0x02, 0x0A, 0x00)), 10), (bytes((0x0B, 0x00)), 11),
    (b'\x05', None)])
def test_next_index(raw: bytes, expected: int | None) -> None:
    assert cover.next_index(Payload(0, [Tlv(0xA2, raw)])) == expected
    assert cover.next_index(Payload(0, [])) is None
