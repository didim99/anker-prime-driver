"""AES-GCM and ECDH tests against captured frames."""

import pytest

from ankerprime.protocol.a2687.crypto import (
    NEGOTIATION_KEYS, CryptoError, EphemeralKey, SessionKeys)
from ankerprime.protocol.a2687.frame import decode_frame
from tests.protocol.a2687.fixtures import CLIENT_FRAMES, DEVICE_FRAMES


@pytest.mark.parametrize(('captured', 'plaintext'),
                         [*CLIENT_FRAMES, *DEVICE_FRAMES])
def test_negotiation_key_opens_captures(captured: bytes,
                                        plaintext: bytes) -> None:
    assert NEGOTIATION_KEYS.open(decode_frame(captured).payload) == plaintext


@pytest.mark.parametrize(('captured', 'plaintext'), CLIENT_FRAMES)
def test_sealing_reproduces_captured_requests(captured: bytes,
                                              plaintext: bytes) -> None:
    # The protocol reuses one nonce per key, so sealing is deterministic.
    assert NEGOTIATION_KEYS.seal(plaintext) == decode_frame(captured).payload


def test_tampered_payload_fails_closed() -> None:
    sealed = bytearray(NEGOTIATION_KEYS.seal(b'\xa1\x01\x00'))
    sealed[0] ^= 1
    with pytest.raises(CryptoError):
        NEGOTIATION_KEYS.open(bytes(sealed))
    with pytest.raises(CryptoError):
        NEGOTIATION_KEYS.open(b'\x00' * 16)


def test_ecdh_gives_both_sides_the_same_session_key() -> None:
    app, charger = EphemeralKey(), EphemeralKey()
    app_keys = SessionKeys.from_shared_secret(
        app.shared_secret(charger.public_point))
    charger_keys = SessionKeys.from_shared_secret(
        charger.shared_secret(app.public_point))
    assert len(app.public_point) == 64
    assert charger_keys.open(app_keys.seal(b'hello')) == b'hello'
    with pytest.raises(CryptoError):
        NEGOTIATION_KEYS.open(app_keys.seal(b'hello'))


def test_invalid_key_material_is_rejected() -> None:
    key = EphemeralKey()
    with pytest.raises(CryptoError):
        key.shared_secret(b'\x01' * 64)
    with pytest.raises(CryptoError):
        key.shared_secret(b'\x01' * 32)
    with pytest.raises(CryptoError):
        SessionKeys.from_shared_secret(b'\x00' * 20)
    with pytest.raises(CryptoError):
        SessionKeys(b'\x00' * 15, b'\x00' * 12)


def test_keys_hide_their_material() -> None:
    assert repr(NEGOTIATION_KEYS) == 'SessionKeys(<hidden>)'
    assert repr(EphemeralKey()) == 'EphemeralKey(<hidden>)'
