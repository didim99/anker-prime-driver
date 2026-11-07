"""AES-GCM and P-256 layer of the A2687 session.

Two key schedules exist: a static negotiation key baked into the firmware,
used until the ECDH exchange completes, and the per-connection session key
(bytes 0..16 of the P-256 shared secret are the AES-128 key, bytes 16..28 the
GCM nonce). The protocol reuses one nonce per key and leaves the frame header
out of the AAD; opening is therefore strictly fail-closed.
"""

from dataclasses import dataclass
from typing import Self

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)

from ankerprime.model.errors import ProtocolError


AAD = bytes.fromhex('3322110077665544bbaa9988ffeeddcc')
NEGOTIATION_KEY = bytes.fromhex('b8ff7422955d4eb6d554a2c470280559')
NEGOTIATION_NONCE = bytes.fromhex('6ba3e3f2f3a60f2971ce5d1f')
TAG_LENGTH = 16
KEY_LENGTH = 16
NONCE_LENGTH = 12
POINT_LENGTH = 64


class CryptoError(ProtocolError):
    """A sealed payload or key material is not usable."""


@dataclass(init=False, repr=False)
class SessionKeys:
    """AES-128-GCM key and its fixed nonce; never logged."""

    _cipher: AESGCM
    _nonce: bytes

    def __init__(self, key: bytes, nonce: bytes):
        if len(key) != KEY_LENGTH or len(nonce) != NONCE_LENGTH:
            raise CryptoError('wrong AES key or nonce length')
        self._cipher = AESGCM(key)
        self._nonce = nonce

    def seal(self, plaintext: bytes) -> bytes:
        """Return ``ciphertext || tag``."""

        return self._cipher.encrypt(self._nonce, plaintext, AAD)

    def open(self, sealed: bytes) -> bytes:
        """Authenticate and decrypt; any defect raises ``CryptoError``."""

        if len(sealed) <= TAG_LENGTH:
            raise CryptoError(f'sealed payload too short: {len(sealed)} bytes')
        try:
            return self._cipher.decrypt(self._nonce, sealed, AAD)
        except InvalidTag:
            raise CryptoError('payload failed authentication') from None

    def __repr__(self) -> str:
        return 'SessionKeys(<hidden>)'

    @classmethod
    def from_shared_secret(cls, secret: bytes) -> Self:
        if len(secret) < KEY_LENGTH + NONCE_LENGTH:
            raise CryptoError(f'shared secret too short: {len(secret)} bytes')
        return cls(secret[:KEY_LENGTH],
                   secret[KEY_LENGTH:KEY_LENGTH + NONCE_LENGTH])


@dataclass(init=False, repr=False)
class EphemeralKey:
    """Fresh P-256 key pair for one connection's key agreement."""

    _private: ec.EllipticCurvePrivateKey

    def __init__(self) -> None:
        self._private = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_point(self) -> bytes:
        """Uncompressed ``X || Y`` without the 04 prefix, as sent on the wire."""

        encoded = self._private.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)
        return encoded[1:]

    def shared_secret(self, device_point: bytes) -> bytes:
        """32-byte ECDH secret with the device's raw 64-byte point."""

        if len(device_point) != POINT_LENGTH:
            raise CryptoError(f'device key has {len(device_point)} bytes')
        try:
            public = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), b'\x04' + device_point)
        except ValueError:
            raise CryptoError('device key is not a P-256 point') from None
        return self._private.exchange(ec.ECDH(), public)

    def __repr__(self) -> str:
        return 'EphemeralKey(<hidden>)'


NEGOTIATION_KEYS = SessionKeys(NEGOTIATION_KEY, NEGOTIATION_NONCE)
