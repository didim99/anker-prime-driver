"""A2687 frame codec tests against captured frames."""

import pytest

from ankerprime.protocol.a2687.codes import (
    NEGOTIATION_GROUP, SESSION_GROUP, Opcode)
from ankerprime.protocol.a2687.frame import (
    Frame, FrameReassembler, decode_frame, encode_frame)
from ankerprime.protocol.ff09 import FrameError
from tests.protocol.a2687.fixtures import (
    CLIENT_BASE_INFO, CLIENT_FRAMES, CLIENT_INITIAL_CONNECT,
    DEVICE_FRAMES, DEVICE_PUBLIC_KEY)


def test_decodes_client_request() -> None:
    frame = decode_frame(CLIENT_INITIAL_CONNECT)
    assert frame.opcode == Opcode.INITIAL_CONNECT
    assert frame.raw_command == 0x4001
    assert frame.encrypted
    assert not frame.response
    assert not frame.from_device
    assert (frame.version, frame.speaker, frame.group) == (3, 0, 1)
    assert len(frame.payload) == 6 + 16


def test_decodes_device_reply() -> None:
    frame = decode_frame(DEVICE_PUBLIC_KEY)
    assert frame.opcode == Opcode.PUBLIC_KEY
    assert frame.raw_command == 0x4821
    assert frame.encrypted and frame.response and frame.from_device
    # This firmware answers with the request's speaker and group.
    assert (frame.speaker, frame.group) == (0, NEGOTIATION_GROUP)


@pytest.mark.parametrize('captured', [
    *(frame for frame, _ in CLIENT_FRAMES), CLIENT_BASE_INFO,
    *(frame for frame, _ in DEVICE_FRAMES)])
def test_reencodes_captures_byte_exact(captured: bytes) -> None:
    assert encode_frame(decode_frame(captured)) == captured


def test_build_sets_flags() -> None:
    frame = Frame.build(SESSION_GROUP, Opcode.PORT_OUTPUT, b'\x01', encrypted=True,
                        response=True)
    assert frame.raw_command == 0x4A07
    assert frame.opcode == Opcode.PORT_OUTPUT
    plain = Frame.build(NEGOTIATION_GROUP, Opcode.BASE_INFO, b'', encrypted=False)
    assert plain.raw_command == Opcode.BASE_INFO
    assert decode_frame(encode_frame(plain)).payload == b''


def test_rejects_corrupt_frame() -> None:
    corrupt = bytearray(CLIENT_INITIAL_CONNECT)
    corrupt[-1] ^= 0xFF
    with pytest.raises(FrameError):
        decode_frame(bytes(corrupt))


def test_reassembles_split_and_coalesced_notifications() -> None:
    reassembler = FrameReassembler()
    stream = DEVICE_PUBLIC_KEY + CLIENT_INITIAL_CONNECT
    frames = []
    for start in range(0, len(stream), 20):
        frames += reassembler.feed(stream[start:start + 20])
    assert [frame.opcode for frame in frames] == [Opcode.PUBLIC_KEY,
                                                  Opcode.INITIAL_CONNECT]
    frames = reassembler.feed(b'\x00\x01garbage' + CLIENT_BASE_INFO)
    assert [frame.opcode for frame in frames] == [Opcode.BASE_INFO]
