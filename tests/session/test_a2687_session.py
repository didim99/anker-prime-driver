"""A2687 session tests against the simulated charger."""

import asyncio

import pytest

from ankerprime.interfaces.channel import ChannelListener
from ankerprime.model.errors import (
    ChargerConnectionError, ChargerTimeoutError, ProtocolError)
from ankerprime.protocol.a2687 import commands
from ankerprime.protocol.a2687.codes import (
    OP_AES_METADATA, OP_BASE_INFO, OP_CAPABILITY, OP_INITIAL_CONNECT,
    OP_PUBLIC_KEY, OP_READ_ALL, OP_SET_CAPABILITY, OP_USER_AUTH)
from ankerprime.protocol.a2687.crypto import NEGOTIATION_KEYS
from ankerprime.protocol.a2687.frame import Frame, decode_frame, encode_frame
from ankerprime.protocol.a2687.handshake import HandshakeStage
from ankerprime.protocol.a2687.tlv import Payload
from ankerprime.session.a2687 import A2687Session
from tests.device.a2687_charger import SimulatedCharger, SimulatedLink


LADDER = [OP_INITIAL_CONNECT, OP_CAPABILITY, OP_BASE_INFO, OP_SET_CAPABILITY,
          OP_PUBLIC_KEY, OP_AES_METADATA, OP_USER_AUTH]


class Recorder(ChannelListener):
    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.closed: list[Exception | None] = []

    def on_message(self, data: bytes) -> None:
        self.frames.append(decode_frame(data))

    def on_closed(self, error: Exception | None) -> None:
        self.closed.append(error)


def make_session(charger: SimulatedCharger, owner_id: str | None = None
                 ) -> tuple[A2687Session, SimulatedLink, Recorder]:
    link = SimulatedLink(charger)
    session = A2687Session(link, client_id='client-1', owner_id=owner_id,
                           time_zone=lambda: 'CET-1', stage_timeout=0.1)
    recorder = Recorder()
    session.set_listener(recorder)
    return session, link, recorder


def plain(message: commands.OutgoingMessage) -> bytes:
    return encode_frame(Frame.build(message.group, message.opcode,
                                    message.plaintext, encrypted=False))


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_negotiates_and_carries_plaintext_frames() -> None:
    charger = SimulatedCharger()
    session, link, recorder = make_session(charger)
    await session.open()
    assert session.is_open
    assert session.stage == HandshakeStage.SESSION_READY
    assert charger.opcodes() == LADDER
    assert charger.identity == 'client-1'
    metadata = dict(charger.requests)[OP_AES_METADATA]
    assert metadata.get(0xA5) == b'CET-1'
    info = session.device_info
    assert info.serial == 'ASHDMOCK00000000'
    assert info.mac_address == '02:00:5E:10:00:01'
    assert not session.auth_rejected
    await session.send(plain(commands.read_all(1)))
    await settle()
    [reply] = recorder.frames
    assert reply.opcode == OP_READ_ALL and reply.response
    assert not reply.encrypted
    assert Payload.parse(reply.payload).ok
    # Sent frames are sealed with the session key, not the static one.
    with pytest.raises(ProtocolError):
        NEGOTIATION_KEYS.open(decode_frame(link.sent[-1]).payload)


async def test_owner_id_is_the_user_identity() -> None:
    charger = SimulatedCharger(required_identity='owner-1')
    session, _, _ = make_session(charger, owner_id='owner-1')
    await session.open()
    assert charger.identity == 'owner-1'
    assert charger.session_open


async def test_silent_metadata_and_auth_are_skipped() -> None:
    charger = SimulatedCharger(silent={OP_AES_METADATA, OP_USER_AUTH})
    session, _, _ = make_session(charger)
    await session.open()
    assert session.is_open
    assert charger.opcodes()[-2:] == [OP_AES_METADATA, OP_USER_AUTH]


async def test_refused_identity_continues_read_only() -> None:
    charger = SimulatedCharger(required_identity='someone-else')
    session, _, _ = make_session(charger)
    await session.open()
    assert session.is_open
    assert session.auth_rejected


async def test_silent_stage_times_out_and_closes() -> None:
    charger = SimulatedCharger(silent={OP_CAPABILITY})
    session, link, _ = make_session(charger)
    with pytest.raises(ChargerTimeoutError):
        await session.open()
    assert not link.is_open
    assert not session.is_open


async def test_link_loss_during_negotiation_fails_open() -> None:
    charger = SimulatedCharger(silent={OP_BASE_INFO})
    session, link, _ = make_session(charger)
    opening = asyncio.create_task(session.open())
    await asyncio.sleep(0.02)
    link.drop()
    with pytest.raises(ChargerConnectionError):
        await opening


async def test_close_and_loss_reach_the_listener() -> None:
    session, link, recorder = make_session(SimulatedCharger())
    await session.open()
    await session.close()
    assert recorder.closed == [None]
    await session.open()
    link.drop()
    assert isinstance(recorder.closed[-1], ChargerConnectionError)
    assert not session.is_open
    with pytest.raises(ChargerConnectionError):
        await session.send(plain(commands.read_all(1)))


async def test_reopen_negotiates_again() -> None:
    charger = SimulatedCharger()
    session, link, _ = make_session(charger)
    await session.open()
    await session.open()
    assert link.opens == 1
    await session.close()
    await session.open()
    assert link.opens == 2
    assert charger.opcodes().count(OP_INITIAL_CONNECT) == 2


async def test_unauthentic_frames_end_the_session() -> None:
    session, link, recorder = make_session(SimulatedCharger())
    await session.open()
    forged = encode_frame(Frame.build(0x0F, 0x0300, b'\x00' * 32,
                                      encrypted=True))
    for _ in range(4):
        link.inject(forged)
    assert session.is_open
    link.inject(forged)
    await settle()
    assert not link.is_open
    assert isinstance(recorder.closed[-1], ProtocolError)
    assert recorder.frames == []


async def test_plaintext_frames_are_accepted() -> None:
    session, link, recorder = make_session(SimulatedCharger())
    await session.open()
    link.inject(encode_frame(Frame.build(0x0F, 0x0300, b'\x00\xa1\x01\x21',
                                         encrypted=False)))
    assert [frame.opcode for frame in recorder.frames] == [0x0300]
