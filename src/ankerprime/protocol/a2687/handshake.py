"""Pure state machine of the A2687 negotiation.

The ladder: initial connect → capability → base info → set capability →
P-256 public key exchange (session key derived) → AES metadata → user auth →
session ready. Each step answers the previous reply; the engine owns no I/O
and no timers. AES metadata and user auth are not acknowledged by some
firmware, so the session may skip them after a timeout or a refusal.
"""

import secrets
from datetime import datetime
from enum import IntEnum

from ankerprime.model.errors import AnkerPrimeError, ProtocolError
from ankerprime.model.state import DeviceInfo
from ankerprime.protocol.a2687.codes import FieldId, Opcode
from ankerprime.protocol.a2687.commands import (
    Encryption, OutgoingMessage, negotiation)
from ankerprime.protocol.a2687.crypto import EphemeralKey, SessionKeys
from ankerprime.protocol.a2687.tlv import Payload
from ankerprime.utils import u32le


AES_METADATA_CAPABILITY = bytes.fromhex('808fffff')
DEFAULT_MTU = 0x0129
DEFAULT_AUTH_METHOD = 0x44
DEFAULT_ENCRYPTION_METHOD = 0x02
MIN_SERIAL_LENGTH = 8
MAC_LENGTH = 6
CLIENT_ID_BYTES = 20
UTC_RULE = 'UTC0'
MIN_ZONE_NAME = 3


class HandshakeStage(IntEnum):
    """What the engine waits for; ``SESSION_READY`` allows commands."""

    IDLE = 0
    INITIAL_CONNECT = 1
    CAPABILITY = 2
    BASE_INFO = 3
    SET_CAPABILITY = 4
    PUBLIC_KEY = 5
    AES_METADATA = 6
    USER_AUTH = 7
    SESSION_READY = 8


AWAITED_OPCODES = {
    HandshakeStage.INITIAL_CONNECT: Opcode.INITIAL_CONNECT,
    HandshakeStage.CAPABILITY: Opcode.CAPABILITY,
    HandshakeStage.BASE_INFO: Opcode.BASE_INFO,
    HandshakeStage.SET_CAPABILITY: Opcode.SET_CAPABILITY,
    HandshakeStage.PUBLIC_KEY: Opcode.PUBLIC_KEY,
    HandshakeStage.AES_METADATA: Opcode.AES_METADATA,
    HandshakeStage.USER_AUTH: Opcode.USER_AUTH,
}
SKIPPABLE_STAGES = (HandshakeStage.AES_METADATA, HandshakeStage.USER_AUTH)


class HandshakeError(ProtocolError):
    """The charger refused or garbled a negotiation step."""

    _stage: HandshakeStage
    _status: int | None

    def __init__(self, stage: HandshakeStage, message: str,
                 status: int | None = None):
        super().__init__(f'handshake failed at {stage.name.lower()}: '
                         f'{message}')
        self._stage = stage
        self._status = status

    @property
    def stage(self) -> HandshakeStage:
        return self._stage

    @property
    def status(self) -> int | None:
        return self._status


class HandshakeEngine:
    """Consumes decrypted replies and produces the next plaintext messages.

    ``owner_id`` is the Anker account the charger is bound to; hardened
    firmware accepts user auth only with it and stays silent otherwise. The
    random ``client_id`` is the fallback older firmware accepts.
    """

    _client_id: str
    _time_zone: str
    _owner_id: str | None

    _stage: HandshakeStage
    _key: EphemeralKey | None
    _session_keys: SessionKeys | None
    _device_info: DeviceInfo
    _mtu: int
    _auth_method: int
    _encryption_method: int
    _auth_rejected: bool

    def __init__(self, client_id: str, time_zone: str,
                 owner_id: str | None = None):
        self._client_id = client_id
        self._time_zone = time_zone
        self._owner_id = owner_id
        self._stage = HandshakeStage.IDLE
        self._key = None
        self._session_keys = None
        self._device_info = DeviceInfo()
        self._mtu = DEFAULT_MTU
        self._auth_method = DEFAULT_AUTH_METHOD
        self._encryption_method = DEFAULT_ENCRYPTION_METHOD
        self._auth_rejected = False

    @property
    def stage(self) -> HandshakeStage:
        return self._stage

    @property
    def session_keys(self) -> SessionKeys | None:
        return self._session_keys

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info

    @property
    def mtu(self) -> int:
        return self._mtu

    @property
    def auth_rejected(self) -> bool:
        """The charger refused user auth; writes are likely ignored."""

        return self._auth_rejected

    @property
    def can_skip(self) -> bool:
        return self._stage in SKIPPABLE_STAGES

    def start(self, timestamp: int) -> OutgoingMessage:
        self._stage = HandshakeStage.INITIAL_CONNECT
        self._key = EphemeralKey()
        return self._message(Opcode.INITIAL_CONNECT,
                             [(FieldId.A1, u32le(timestamp))])

    def handle(self, opcode: int, payload: Payload,
               timestamp: int) -> list[OutgoingMessage]:
        """Advance on the awaited reply; other frames change nothing.

        A refusal raises ``HandshakeError``; for a skippable stage the caller
        may continue with ``skip``.
        """

        if self._stage == HandshakeStage.IDLE:
            raise HandshakeError(self._stage, 'not started')
        if AWAITED_OPCODES.get(self._stage) != opcode:
            return []
        if not payload.ok:
            if self._stage == HandshakeStage.USER_AUTH:
                self._auth_rejected = True
            raise HandshakeError(self._stage, 'refused by the charger',
                                 payload.status)
        return self._advance(payload, timestamp)

    def skip(self, timestamp: int) -> list[OutgoingMessage]:
        """Move past an unacknowledged AES metadata or user auth step."""

        if not self.can_skip:
            raise HandshakeError(self._stage, 'step cannot be skipped')
        if self._stage == HandshakeStage.AES_METADATA:
            self._stage = HandshakeStage.USER_AUTH
            return [self._user_auth(timestamp)]
        self._stage = HandshakeStage.SESSION_READY
        return []

    def _advance(self, payload: Payload,
                 timestamp: int) -> list[OutgoingMessage]:
        stamp = (FieldId.A1, u32le(timestamp))
        match self._stage:
            case HandshakeStage.INITIAL_CONNECT:
                self._stage = HandshakeStage.CAPABILITY
                return [self._message(Opcode.CAPABILITY, [
                    stamp, (FieldId.A3, b'\x20'), (FieldId.A4, b'\x00\xf0')])]
            case HandshakeStage.CAPABILITY:
                self._read_capability(payload)
                self._stage = HandshakeStage.BASE_INFO
                return [self._message(Opcode.BASE_INFO, [stamp])]
            case HandshakeStage.BASE_INFO:
                self._device_info = decode_device_info(payload)
                self._stage = HandshakeStage.SET_CAPABILITY
                return [self._message(Opcode.SET_CAPABILITY, [
                    stamp, (FieldId.A3, b'\x20'),
                    (FieldId.A4, self._mtu.to_bytes(2, 'little')),
                    (FieldId.A5, bytes((self._auth_method,))),
                    (FieldId.A6, bytes((self._encryption_method,)))])]
            case HandshakeStage.SET_CAPABILITY:
                self._stage = HandshakeStage.PUBLIC_KEY
                return [self._message(Opcode.PUBLIC_KEY, [
                    (FieldId.A1, self._ephemeral_key().public_point)])]
            case HandshakeStage.PUBLIC_KEY:
                self._derive_session_keys(payload)
                self._stage = HandshakeStage.AES_METADATA
                return [self._message(Opcode.AES_METADATA, [
                    stamp, (FieldId.A3, AES_METADATA_CAPABILITY),
                    (FieldId.A5, self._time_zone.encode())])]
            case HandshakeStage.AES_METADATA:
                self._stage = HandshakeStage.USER_AUTH
                return [self._user_auth(timestamp)]
            case _:
                self._stage = HandshakeStage.SESSION_READY
                return []

    def _read_capability(self, payload: Payload) -> None:
        mtu = payload.get(FieldId.A2)
        if mtu is not None and len(mtu) >= 2:
            self._mtu = int.from_bytes(mtu[:2], 'little')
        auth = payload.get(FieldId.A3)
        if auth:
            self._auth_method = auth[0]
        encryption = payload.get(FieldId.A5)
        if encryption:
            self._encryption_method = encryption[0]

    def _derive_session_keys(self, payload: Payload) -> None:
        point = payload.get(FieldId.A1)
        if point is None:
            raise HandshakeError(self._stage, 'no device key')
        try:
            secret = self._ephemeral_key().shared_secret(point)
            self._session_keys = SessionKeys.from_shared_secret(secret)
        except AnkerPrimeError as error:
            raise HandshakeError(self._stage, str(error)) from error
        # The private key has done its job.
        self._key = None

    def _ephemeral_key(self) -> EphemeralKey:
        if self._key is None:
            raise HandshakeError(self._stage, 'no key pair')
        return self._key

    def _user_auth(self, timestamp: int) -> OutgoingMessage:
        identity = self._owner_id or self._client_id
        return self._message(Opcode.USER_AUTH,
                             [(FieldId.A1, u32le(timestamp)),
                              (FieldId.A2, identity.encode())])

    def _message(self, opcode: int,
                 fields: list[tuple[int, bytes]]) -> OutgoingMessage:
        encryption = (Encryption.SESSION
                      if self._stage > HandshakeStage.PUBLIC_KEY
                      else Encryption.NEGOTIATION)
        return negotiation(opcode, fields, encryption)


def decode_device_info(payload: Payload) -> DeviceInfo:
    """Base info: A2 product name, A3 firmware, A4 serial, A5 MAC + suffix."""

    serial = _ascii(payload.get(FieldId.A4))
    mac = None
    raw = payload.get(FieldId.A5)
    if raw is not None and len(raw) >= MAC_LENGTH:
        prefix = raw[:MAC_LENGTH]
        if _ascii(prefix) is None:
            mac = ':'.join(f'{byte:02X}' for byte in prefix)
    return DeviceInfo(
        serial=serial if serial and len(serial) >= MIN_SERIAL_LENGTH else None,
        name=_ascii(payload.get(FieldId.A2)),
        firmware_version=_ascii(payload.get(FieldId.A3)), mac_address=mac)


def _ascii(raw: bytes | None) -> str | None:
    if not raw or not all(0x20 <= byte < 0x7F for byte in raw):
        return None
    return raw.decode('ascii')


def new_client_id() -> str:
    """Random identity for user auth: 40 hex characters, as Charker uses."""

    return secrets.token_hex(CLIENT_ID_BYTES)


def posix_time_zone(now: datetime | None = None) -> str:
    """Best-effort POSIX TZ rule of the zone of ``now`` (default: local).

    Only the current offset is described (no DST rule); POSIX inverts the
    sign, so UTC+2 is ``CEST-2``. Zone names shorter than three letters,
    such as numeric ones, become ``UTC``.
    """

    local = now if now is not None else datetime.now()
    if local.tzinfo is None:
        local = local.astimezone()
    offset = local.utcoffset()
    seconds = int(offset.total_seconds()) if offset is not None else 0
    if seconds == 0:
        return UTC_RULE
    name = ''
    for char in local.tzname() or '':
        if not char.isalpha():
            break
        name += char
    if len(name) < MIN_ZONE_NAME:
        name = 'UTC'
    sign = '-' if seconds > 0 else ''
    hours, rest = divmod(abs(seconds), 3600)
    minutes = rest // 60
    if minutes:
        return f'{name}{sign}{hours}:{minutes:02d}'
    return f'{name}{sign}{hours}'
