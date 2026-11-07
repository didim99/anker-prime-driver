"""FF09 frame codec and reassembler tests."""

import pytest

from ankerprime.protocol.a2345.frame import (
    Frame, FrameReassembler, Tlv, decode_frame, encode_request)
from ankerprime.protocol.ff09 import (
    MAX_FRAME_LENGTH, FrameError, FrameErrorKind, checksum)
from tests.protocol.a2345.fixtures import (
    REALTIME_ACKNOWLEDGEMENT, REALTIME_NEW_FIRMWARE,
    REALTIME_OLD_FIRMWARE, REALTIME_TRIGGER_REQUEST,
    REQUEST_EPOCH, STATUS_SNAPSHOT_REQUEST, VERSION_INFO)


def with_checksum(data: bytearray) -> bytes:
    data[-1] = checksum(data[:-1])
    return bytes(data)


def message_types(frames: list[Frame]) -> list[int]:
    return [frame.message_type for frame in frames]


def test_decodes_frame_with_increment() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    assert frame.message_type == 0x0303
    assert (frame.version, frame.speaker, frame.group) == (3, 1, 15)
    assert frame.increment == 1
    assert [field.tag for field in frame.fields] == [
        0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xFE]
    assert frame.length == 100


def test_decodes_frame_without_increment() -> None:
    assert len(VERSION_INFO) == 62
    frame = decode_frame(VERSION_INFO)
    assert frame.message_type == 0x0830
    assert frame.increment is None
    assert [field.tag for field in frame.fields] == [
        0xA1, 0xA2, 0xA3, 0xA4, 0xA5]


def test_field_lookup_and_value_type() -> None:
    frame = decode_frame(REALTIME_ACKNOWLEDGEMENT)
    marker = frame.field(0xA1)
    assert marker == Tlv(0xA1, b'\x34')
    assert marker is not None and marker.value_type is None
    timestamp = decode_frame(REALTIME_OLD_FIRMWARE).field(0xFE)
    assert timestamp is not None and timestamp.value_type == 0x03
    assert frame.field(0xA2) is None


def test_rejects_bad_header() -> None:
    data = bytearray(REALTIME_OLD_FIRMWARE)
    data[0] = 0xFE
    with pytest.raises(FrameError) as exc_info:
        decode_frame(bytes(data))
    assert exc_info.value.kind == FrameErrorKind.BAD_HEADER


def test_rejects_length_mismatch() -> None:
    data = bytearray(REALTIME_OLD_FIRMWARE)
    data[2] -= 1
    with pytest.raises(FrameError) as exc_info:
        decode_frame(bytes(data))
    assert exc_info.value.kind == FrameErrorKind.LENGTH_MISMATCH


def test_rejects_bad_checksum() -> None:
    data = bytearray(REALTIME_OLD_FIRMWARE)
    data[40] ^= 0x01
    with pytest.raises(FrameError) as exc_info:
        decode_frame(bytes(data))
    assert exc_info.value.kind == FrameErrorKind.BAD_CHECKSUM


def test_rejects_unsupported_version_with_valid_checksum() -> None:
    data = bytearray(REALTIME_ACKNOWLEDGEMENT)
    data[4] = 4
    with pytest.raises(FrameError) as exc_info:
        decode_frame(with_checksum(data))
    assert exc_info.value.kind == FrameErrorKind.UNSUPPORTED_VERSION


def test_rejects_truncated_field() -> None:
    data = bytearray(REALTIME_ACKNOWLEDGEMENT)
    data[11] = 2  # A1 claims two bytes, only one is left before the checksum
    with pytest.raises(FrameError) as exc_info:
        decode_frame(with_checksum(data))
    assert exc_info.value.kind == FrameErrorKind.TRUNCATED_FIELD


@pytest.mark.parametrize(('data', 'kind'), [
    (b'\xff\x09', FrameErrorKind.TOO_SHORT),
    (bytes(MAX_FRAME_LENGTH + 1), FrameErrorKind.TOO_LARGE),
])
def test_rejects_size_limits(data: bytes, kind: FrameErrorKind) -> None:
    with pytest.raises(FrameError) as exc_info:
        decode_frame(data)
    assert exc_info.value.kind == kind


def test_every_truncated_prefix_is_rejected() -> None:
    for end in range(len(REALTIME_NEW_FIRMWARE)):
        with pytest.raises(FrameError):
            decode_frame(REALTIME_NEW_FIRMWARE[:end])


@pytest.mark.parametrize(('message_type', 'expected'), [
    (0x0200, STATUS_SNAPSHOT_REQUEST),
    (0x020B, REALTIME_TRIGGER_REQUEST),
])
def test_read_requests_match_fixed_vectors(
        message_type: int, expected: bytes) -> None:
    assert encode_request(message_type, {}, timestamp=REQUEST_EPOCH) == expected


def test_read_request_round_trip() -> None:
    frame = decode_frame(STATUS_SNAPSHOT_REQUEST)
    assert (frame.version, frame.speaker, frame.group) == (3, 0, 15)
    assert frame.increment is None
    assert frame.length == 20
    assert frame.fields == (
        Tlv(0xA1, b'\x22'), Tlv(0xFE, b'\x03\x00\xf1\x53\x65'))


def test_request_fields_are_sorted_and_typed() -> None:
    data = encode_request(0x0207, {0xA3: b'\x01', 0xA2: b'\x04'},
                          timestamp=REQUEST_EPOCH)
    frame = decode_frame(data)
    assert frame.fields[:3] == (
        Tlv(0xA1, b'\x22'), Tlv(0xA2, b'\x01\x04'), Tlv(0xA3, b'\x01\x01'))


@pytest.mark.parametrize(('value', 'value_type'), [
    (b'\x01', 0x01), (b'\x01\x02', 0x02), (b'\x01\x02\x03\x04', 0x03),
    (b'\x01\x02\x03', 0x04), (b'', 0x04),
])
def test_request_value_types(value: bytes, value_type: int) -> None:
    data = encode_request(0x0200, {0xA2: value}, timestamp=REQUEST_EPOCH)
    frame = decode_frame(data)
    assert frame.fields[1] == Tlv(0xA2, bytes((value_type,)) + value)


def test_request_session_byte_is_configurable() -> None:
    data = encode_request(0x0200, {}, timestamp=REQUEST_EPOCH, session=0x11)
    assert decode_frame(data).field(0xA1) == Tlv(0xA1, b'\x11')


def test_long_field_uses_two_byte_length() -> None:
    value = bytes(200)
    data = encode_request(0x0205, {0xA7: value}, timestamp=REQUEST_EPOCH,
                          allow_long_fields=True)
    assert data[12:15] == b'\xa7\xc9\x00'
    assert len(data) == 12 + 3 + 1 + len(value) + 7 + 1


def test_reassembles_every_split_boundary() -> None:
    for split in range(1, len(REALTIME_NEW_FIRMWARE)):
        reassembler = FrameReassembler()
        assert reassembler.feed(REALTIME_NEW_FIRMWARE[:split]) == []
        frames = reassembler.feed(REALTIME_NEW_FIRMWARE[split:])
        assert message_types(frames) == [0x0303]


def test_splits_coalesced_frames_and_keeps_trailing_marker_byte() -> None:
    reassembler = FrameReassembler()
    frames = reassembler.feed(REALTIME_ACKNOWLEDGEMENT + VERSION_INFO + b'\xff')
    assert message_types(frames) == [0x0A0B, 0x0830]
    assert reassembler.dropped_bytes == 0
    frames = reassembler.feed(REALTIME_ACKNOWLEDGEMENT[1:])
    assert message_types(frames) == [0x0A0B]


def test_recovers_valid_frame_after_malformed_candidate() -> None:
    corrupt = bytearray(REALTIME_ACKNOWLEDGEMENT)
    corrupt[-1] ^= 1
    reassembler = FrameReassembler()
    frames = reassembler.feed(bytes(corrupt) + VERSION_INFO)
    assert message_types(frames) == [0x0830]
    assert reassembler.rejected_frames == 1
    assert reassembler.last_rejection is not None


def test_implausible_length_does_not_stall_stream() -> None:
    reassembler = FrameReassembler()
    assert reassembler.feed(b'\xff\x09\x02\x00') == []
    frames = reassembler.feed(REALTIME_ACKNOWLEDGEMENT)
    assert message_types(frames) == [0x0A0B]
    assert reassembler.rejected_frames == 1


def test_false_length_recovers_on_later_valid_frame() -> None:
    false_candidate = b'\xff\x09\x00\x08'
    reassembler = FrameReassembler()
    frames = reassembler.feed(false_candidate + REALTIME_ACKNOWLEDGEMENT)
    assert message_types(frames) == [0x0A0B]
    assert reassembler.rejected_frames == 1
    assert reassembler.dropped_bytes == len(false_candidate)
    rejection = reassembler.last_rejection
    assert rejection is not None
    assert rejection.kind == FrameErrorKind.LENGTH_MISMATCH


def test_garbage_is_dropped_and_reset_clears_buffer() -> None:
    reassembler = FrameReassembler()
    assert reassembler.feed(b'\x00\x01\x02' + REALTIME_ACKNOWLEDGEMENT[:5]) == []
    assert reassembler.dropped_bytes == 3
    reassembler.reset()
    assert reassembler.feed(REALTIME_ACKNOWLEDGEMENT[5:]) == []
    frames = reassembler.feed(REALTIME_ACKNOWLEDGEMENT)
    assert message_types(frames) == [0x0A0B]
