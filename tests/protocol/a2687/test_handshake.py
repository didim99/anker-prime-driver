"""Negotiation state machine tests, walking the captured replies."""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ankerprime.protocol.a2687.codes import Opcode
from ankerprime.protocol.a2687.commands import Encryption
from ankerprime.protocol.a2687.crypto import EphemeralKey, SessionKeys
from ankerprime.protocol.a2687.handshake import (
    HandshakeEngine, HandshakeError,
    HandshakeStage, new_client_id, posix_time_zone)
from ankerprime.protocol.a2687.tlv import Payload
from tests.protocol.a2687.fixtures import (
    CAPTURE_EPOCH, PLAIN_CLIENT_CAPABILITY,
    PLAIN_CLIENT_INITIAL_CONNECT, PLAIN_DEVICE_BASE_INFO,
    PLAIN_DEVICE_CAPABILITY, PLAIN_DEVICE_INITIAL_CONNECT,
    PLAIN_DEVICE_PUBLIC_KEY, PLAIN_DEVICE_SET_CAPABILITY)


CLIENT_ID = '79ebed35dc9c4904b40c72c4e863aa10'
OK = Payload.parse(b'\x00')


def engine(owner_id: str | None = None) -> HandshakeEngine:
    return HandshakeEngine(CLIENT_ID, 'CST-8', owner_id)


def until_public_key(handshake: HandshakeEngine) -> None:
    handshake.start(CAPTURE_EPOCH)
    for opcode, plaintext in (
            (Opcode.INITIAL_CONNECT, PLAIN_DEVICE_INITIAL_CONNECT),
            (Opcode.CAPABILITY, PLAIN_DEVICE_CAPABILITY),
            (Opcode.BASE_INFO, PLAIN_DEVICE_BASE_INFO),
            (Opcode.SET_CAPABILITY, PLAIN_DEVICE_SET_CAPABILITY),
            (Opcode.PUBLIC_KEY, PLAIN_DEVICE_PUBLIC_KEY)):
        handshake.handle(opcode, Payload.parse(plaintext), CAPTURE_EPOCH)


def test_ladder_reproduces_the_captured_requests() -> None:
    handshake = engine()
    first = handshake.start(CAPTURE_EPOCH)
    assert first.plaintext == PLAIN_CLIENT_INITIAL_CONNECT
    assert first.encryption == Encryption.NEGOTIATION
    assert handshake.stage == HandshakeStage.INITIAL_CONNECT
    [capability] = handshake.handle(
        Opcode.INITIAL_CONNECT, Payload.parse(PLAIN_DEVICE_INITIAL_CONNECT),
        CAPTURE_EPOCH)
    assert capability.plaintext == PLAIN_CLIENT_CAPABILITY
    [base_info] = handshake.handle(
        Opcode.CAPABILITY, Payload.parse(PLAIN_DEVICE_CAPABILITY), CAPTURE_EPOCH)
    assert base_info.opcode == Opcode.BASE_INFO
    assert handshake.mtu == 0x0129


def test_base_info_and_set_capability() -> None:
    handshake = engine()
    handshake.start(CAPTURE_EPOCH)
    handshake.handle(Opcode.INITIAL_CONNECT,
                     Payload.parse(PLAIN_DEVICE_INITIAL_CONNECT), 1)
    handshake.handle(Opcode.CAPABILITY, Payload.parse(PLAIN_DEVICE_CAPABILITY), 1)
    [message] = handshake.handle(
        Opcode.BASE_INFO, Payload.parse(PLAIN_DEVICE_BASE_INFO), 1)
    info = handshake.device_info
    assert info.name == 'Charging'
    assert info.firmware_version == 'v0.0.5.0'
    assert info.serial == 'ASHDK7U1F51501771'
    assert info.mac_address == '7C:E9:13:81:46:02'
    fields = Payload.parse(message.plaintext)
    assert message.opcode == Opcode.SET_CAPABILITY
    assert (fields.get(0xA4), fields.get(0xA5), fields.get(0xA6)) == (
        b'\x29\x01', b'\x44', b'\x02')


def test_public_key_exchange_moves_to_the_session_key() -> None:
    handshake = engine()
    handshake.start(CAPTURE_EPOCH)
    for opcode, plaintext in (
            (Opcode.INITIAL_CONNECT, PLAIN_DEVICE_INITIAL_CONNECT),
            (Opcode.CAPABILITY, PLAIN_DEVICE_CAPABILITY),
            (Opcode.BASE_INFO, PLAIN_DEVICE_BASE_INFO)):
        handshake.handle(opcode, Payload.parse(plaintext), 1)
    [key] = handshake.handle(Opcode.SET_CAPABILITY,
                             Payload.parse(PLAIN_DEVICE_SET_CAPABILITY), 1)
    assert key.encryption == Encryption.NEGOTIATION
    point = Payload.parse(key.plaintext).get(0xA1)
    assert point is not None and len(point) == 64
    assert handshake.session_keys is None
    charger = EphemeralKey()
    reply = Payload.parse(b'\x00\xa1\x40' + charger.public_point)
    [metadata] = handshake.handle(Opcode.PUBLIC_KEY, reply, 1)
    assert handshake.stage == HandshakeStage.AES_METADATA
    assert metadata.encryption == Encryption.SESSION
    fields = Payload.parse(metadata.plaintext)
    assert fields.get(0xA3) == bytes.fromhex('808fffff')
    assert fields.get(0xA5) == b'CST-8'
    keys = handshake.session_keys
    assert keys is not None
    charger_keys = SessionKeys.from_shared_secret(
        charger.shared_secret(point))
    assert charger_keys.open(keys.seal(b'ping')) == b'ping'


def test_user_auth_uses_the_owner_id_when_given() -> None:
    for owner, identity in ((None, CLIENT_ID), ('owner-1', 'owner-1')):
        handshake = engine(owner)
        until_public_key(handshake)
        [auth] = handshake.handle(Opcode.AES_METADATA, OK, 1)
        assert auth.opcode == Opcode.USER_AUTH
        assert auth.encryption == Encryption.SESSION
        assert Payload.parse(auth.plaintext).get(0xA2) == identity.encode()
        assert handshake.handle(Opcode.USER_AUTH, OK, 1) == []
        assert handshake.stage == HandshakeStage.SESSION_READY
        assert not handshake.auth_rejected


def test_unrelated_frames_are_ignored() -> None:
    handshake = engine()
    handshake.start(1)
    assert handshake.handle(Opcode.REALTIME_REPORT, OK, 1) == []
    assert handshake.handle(Opcode.USER_AUTH, OK, 1) == []
    assert handshake.stage == HandshakeStage.INITIAL_CONNECT


def test_refusal_stops_the_ladder() -> None:
    handshake = engine()
    handshake.start(1)
    with pytest.raises(HandshakeError) as caught:
        handshake.handle(Opcode.INITIAL_CONNECT, Payload(3, ()), 1)
    assert caught.value.stage == HandshakeStage.INITIAL_CONNECT
    assert caught.value.status == 3
    assert not handshake.can_skip


def test_only_metadata_and_user_auth_may_be_skipped() -> None:
    handshake = engine()
    handshake.start(1)
    with pytest.raises(HandshakeError):
        handshake.skip(1)
    until_public_key(handshake)
    assert handshake.can_skip
    assert [message.opcode for message in handshake.skip(1)] == [Opcode.USER_AUTH]
    with pytest.raises(HandshakeError):
        handshake.handle(Opcode.USER_AUTH, Payload(1, ()), 1)
    assert handshake.auth_rejected
    assert handshake.skip(1) == []
    assert handshake.stage == HandshakeStage.SESSION_READY


def test_missing_or_bad_device_key_fails() -> None:
    for reply in (OK, Payload.parse(b'\x00\xa1\x40' + b'\x01' * 64)):
        handshake = engine()
        handshake.start(1)
        for opcode, plaintext in (
                (Opcode.INITIAL_CONNECT, PLAIN_DEVICE_INITIAL_CONNECT),
                (Opcode.CAPABILITY, PLAIN_DEVICE_CAPABILITY),
                (Opcode.BASE_INFO, PLAIN_DEVICE_BASE_INFO),
                (Opcode.SET_CAPABILITY, PLAIN_DEVICE_SET_CAPABILITY)):
            handshake.handle(opcode, Payload.parse(plaintext), 1)
        with pytest.raises(HandshakeError):
            handshake.handle(Opcode.PUBLIC_KEY, reply, 1)


def test_handling_before_start_is_refused() -> None:
    with pytest.raises(HandshakeError):
        engine().handle(Opcode.INITIAL_CONNECT, OK, 1)


def test_client_id_is_random_hex() -> None:
    first = new_client_id()
    assert re.fullmatch('[0-9a-f]{40}', first)
    assert new_client_id() != first


@pytest.mark.parametrize(('zone', 'rule'), [
    (UTC, 'UTC0'),
    (timezone(timedelta(hours=-5), 'EST'), 'EST5'),
    (timezone(timedelta(hours=8), 'CST'), 'CST-8'),
    (timezone(timedelta(hours=5, minutes=30), 'IST'), 'IST-5:30'),
    (timezone(timedelta(hours=3)), 'UTC-3'),
    (timezone(timedelta(hours=-3), '-03'), 'UTC3'),
])
def test_posix_time_zone(zone: timezone, rule: str) -> None:
    assert posix_time_zone(datetime(2026, 11, 7, tzinfo=zone)) == rule
