"""Encrypted A2687 session over a byte transport (BLE notifications/writes).

``open`` opens the transport and runs the negotiation; it returns once the
session is ready. Above it the session is a channel of plaintext frames: the
listener receives each charger frame with its payload decrypted and the
encrypted flag cleared, and ``send`` takes plaintext frames and seals them
with the session key.

Rules from Charker:

- Each negotiation step must be answered within the stage timeout. AES
  metadata and user auth are not acknowledged by every firmware: on silence
  or a refusal they are skipped and the session continues; after a refused
  user auth the charger may ignore writes or stay silent (``auth_rejected``).
- Replies are opened with the key of the current stage first and the other
  key second; plaintext frames are accepted as they are. Five frames in a
  row that fail authentication end the session.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.errors import (
    AnkerPrimeError, ChargerConnectionError, ChargerTimeoutError, ProtocolError)
from ankerprime.model.state import DeviceInfo
from ankerprime.protocol.a2687.codes import ENCRYPTED_FLAG
from ankerprime.protocol.a2687.commands import Encryption, OutgoingMessage
from ankerprime.protocol.a2687.crypto import (
    NEGOTIATION_KEYS, CryptoError, SessionKeys)
from ankerprime.protocol.a2687.frame import (
    Frame, FrameReassembler, decode_frame, encode_frame)
from ankerprime.protocol.a2687.handshake import (
    HandshakeEngine, HandshakeError,
    HandshakeStage, new_client_id, posix_time_zone)
from ankerprime.protocol.a2687.tlv import Payload
from ankerprime.utils import Clock, utc_now


logger = logging.getLogger(__name__)

DEFAULT_STAGE_TIMEOUT = 8.0
MAX_AUTHENTICATION_FAILURES = 5

# What the negotiation loop waits for: a reply or the loss of the transport.
InboxItem = tuple[int, Payload] | ChargerConnectionError


class A2687Session(Channel, ChannelListener):
    """Negotiates keys and user auth, then carries plaintext frames."""

    _transport: Channel
    _client_id: str
    _owner_id: str | None
    _time_zone: Callable[[], str]
    _stage_timeout: float
    _clock: Clock

    _listener: ChannelListener | None
    _engine: HandshakeEngine
    _reassembler: FrameReassembler
    _inbox: asyncio.Queue[InboxItem]
    _ready: bool
    _failures: int
    _closing: asyncio.Task[None] | None

    def __init__(self, transport: Channel, *, client_id: str | None = None,
                 owner_id: str | None = None,
                 time_zone: Callable[[], str] = posix_time_zone,
                 stage_timeout: float = DEFAULT_STAGE_TIMEOUT,
                 clock: Clock | None = None):
        self._transport = transport
        self._client_id = client_id or new_client_id()
        self._owner_id = owner_id or None
        self._time_zone = time_zone
        self._stage_timeout = stage_timeout
        self._clock = clock or utc_now
        self._listener = None
        self._engine = self._new_engine()
        self._reassembler = FrameReassembler()
        self._inbox = asyncio.Queue()
        self._ready = False
        self._failures = 0
        self._closing = None

    @property
    def is_open(self) -> bool:
        return self._ready and self._transport.is_open

    @property
    def stage(self) -> HandshakeStage:
        return self._engine.stage

    @property
    def device_info(self) -> DeviceInfo:
        """Identity from the last negotiation's base info reply."""

        return self._engine.device_info

    @property
    def auth_rejected(self) -> bool:
        """The charger refused the user identity of the last negotiation."""

        return self._engine.auth_rejected

    def set_listener(self, listener: ChannelListener | None) -> None:
        self._listener = listener

    async def open(self) -> None:
        if self.is_open:
            return
        self._ready = False
        self._engine = self._new_engine()
        self._reassembler = FrameReassembler()
        self._inbox = asyncio.Queue()
        self._failures = 0
        self._transport.set_listener(self)
        await self._transport.open()
        try:
            await self._negotiate()
        except BaseException:
            await self._transport.close()
            raise
        self._ready = True

    async def close(self) -> None:
        await self._transport.close()

    async def send(self, data: bytes) -> None:
        keys = self._engine.session_keys
        if not self.is_open or keys is None:
            raise ChargerConnectionError('A2687 session is not open')
        frame = decode_frame(data)
        sealed = Frame.build(frame.group, frame.opcode,
                             keys.seal(frame.payload), encrypted=True)
        await self._transport.send(encode_frame(sealed))

    def on_message(self, data: bytes) -> None:
        for frame in self._reassembler.feed(data):
            self._receive(frame)

    def on_closed(self, error: Exception | None) -> None:
        if not self._ready:
            self._inbox.put_nowait(ChargerConnectionError(
                f'link lost during negotiation: {error}'))
            return
        self._ready = False
        if self._listener is not None:
            self._listener.on_closed(error)

    async def _negotiate(self) -> None:
        engine = self._engine
        await self._send_message(engine.start(self._timestamp()))
        while engine.stage != HandshakeStage.SESSION_READY:
            try:
                item = await asyncio.wait_for(self._inbox.get(),
                                              self._stage_timeout)
            except TimeoutError:
                if not engine.can_skip:
                    raise ChargerTimeoutError(
                        f'no answer at {engine.stage.name.lower()}') from None
                logger.debug('no answer at %s; continuing',
                             engine.stage.name.lower())
                messages = engine.skip(self._timestamp())
            else:
                if isinstance(item, ChargerConnectionError):
                    raise item
                messages = self._advance(*item)
            for message in messages:
                await self._send_message(message)

    def _advance(self, opcode: int, payload: Payload) -> list[OutgoingMessage]:
        engine = self._engine
        try:
            return engine.handle(opcode, payload, self._timestamp())
        except HandshakeError as error:
            if not engine.can_skip:
                raise
            logger.warning('%s; continuing, writes may be refused', error)
            return engine.skip(self._timestamp())

    def _receive(self, frame: Frame) -> None:
        plaintext = self._open_payload(frame)
        if plaintext is None:
            self._failures += 1
            logger.debug('A2687 frame %#06x failed authentication',
                         frame.opcode)
            if self._failures >= MAX_AUTHENTICATION_FAILURES:
                self._fail(ProtocolError('A2687 frames keep failing '
                                         'authentication'))
            return
        self._failures = 0
        if not self._ready:
            if frame.response:
                try:
                    payload = Payload.parse(plaintext)
                except ProtocolError as error:
                    logger.debug('ignored negotiation reply: %s', error)
                    return
                self._inbox.put_nowait((frame.opcode, payload))
            return
        if self._listener is not None:
            plain = Frame(frame.group, frame.raw_command & ~ENCRYPTED_FLAG,
                          plaintext, speaker=frame.speaker,
                          version=frame.version)
            self._listener.on_message(encode_frame(plain))

    def _open_payload(self, frame: Frame) -> bytes | None:
        if not frame.encrypted:
            # Some firmware has been seen reporting in the clear.
            return frame.payload
        session = self._engine.session_keys
        candidates: tuple[SessionKeys | None, ...] = (NEGOTIATION_KEYS, session)
        if self._engine.stage > HandshakeStage.PUBLIC_KEY:
            candidates = (session, NEGOTIATION_KEYS)
        for keys in candidates:
            if keys is None:
                continue
            try:
                return keys.open(frame.payload)
            except CryptoError:
                continue
        return None

    def _fail(self, error: AnkerPrimeError) -> None:
        """Drop the transport after a fatal defect of the stream."""

        if self._closing is not None and not self._closing.done():
            return
        if not self._ready:
            self._inbox.put_nowait(ChargerConnectionError(str(error)))
        self._closing = asyncio.get_running_loop().create_task(
            self._drop(error))

    async def _drop(self, error: AnkerPrimeError) -> None:
        listener = self._listener if self._ready else None
        self._ready = False
        await self._transport.close()
        if listener is not None:
            listener.on_closed(error)

    async def _send_message(self, message: OutgoingMessage) -> None:
        keys = NEGOTIATION_KEYS
        if message.encryption == Encryption.SESSION:
            session = self._engine.session_keys
            if session is None:
                raise ProtocolError('no session key for a session message')
            keys = session
        frame = Frame.build(message.group, message.opcode,
                            keys.seal(message.plaintext), encrypted=True)
        await self._transport.send(encode_frame(frame))

    def _new_engine(self) -> HandshakeEngine:
        return HandshakeEngine(self._client_id, self._time_zone(),
                               self._owner_id)

    def _timestamp(self) -> int:
        return int(self._clock().timestamp())
