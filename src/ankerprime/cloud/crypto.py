"""Login password encryption expected by the Anker passport API.

The password is encrypted to Anker's fixed P-256 public key: ECDH gives a
32-byte secret used as the AES-256-CBC key, with its first 16 bytes as the IV.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)

from ankerprime.model.errors import InvalidParameterError


SERVER_PUBLIC_KEY = bytes.fromhex(
    '04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a'
    '3868d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076')
KEY_LENGTH = 32
IV_LENGTH = 16


class LoginCipher:
    """One ephemeral key pair and the password encryption derived from it."""

    _private_key: ec.EllipticCurvePrivateKey
    _shared_key: bytes

    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None):
        if private_key is None:
            private_key = ec.generate_private_key(ec.SECP256R1())
        server_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), SERVER_PUBLIC_KEY)
        self._private_key = private_key
        self._shared_key = private_key.exchange(ec.ECDH(), server_key)

    @property
    def public_key_hex(self) -> str:
        """Client public key as a hex uncompressed point, sent with the login."""

        public_key = self._private_key.public_key()
        point = public_key.public_bytes(Encoding.X962,
                                        PublicFormat.UncompressedPoint)
        return point.hex()

    @property
    def shared_key(self) -> bytes:
        return self._shared_key

    def encrypt(self, text: str) -> str:
        return encrypt_password(text, self._shared_key)

    @classmethod
    def from_private_value(cls, value: bytes) -> LoginCipher:
        """Build from a fixed 32-byte private scalar (test vectors)."""

        number = int.from_bytes(value, 'big')
        return cls(ec.derive_private_key(number, ec.SECP256R1()))


def encrypt_password(text: str, key: bytes) -> str:
    """AES-256-CBC with PKCS7 padding, base64 encoded."""

    if len(key) != KEY_LENGTH:
        raise InvalidParameterError('login key must be 32 bytes')
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    data = padder.update(text.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:IV_LENGTH]))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(data) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('ascii')
