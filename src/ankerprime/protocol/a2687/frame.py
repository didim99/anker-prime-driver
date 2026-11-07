"""A2687 frames inside the shared FF09 envelope.

Payload layout::

    version speaker group   speaker 0x00 from the app, 0x01 from the charger;
                            the group also differs between request and reply
    uint16 BE command       opcode | 0x4000 when sealed | 0x0800 for replies
    payload                 plaintext TLVs, or AES-GCM ciphertext || tag

The speaker and group bytes are carried through, never asserted: replies use
other values than requests.
"""

from dataclasses import dataclass
from typing import Self

from ankerprime.protocol import ff09
from ankerprime.protocol.a2687.codes import ENCRYPTED_FLAG, RESPONSE_FLAG
from ankerprime.protocol.ff09 import (
    HEADER, PAYLOAD_OFFSET, PROTOCOL_VERSION, seal_frame, validate_envelope)


APP_SPEAKER = 0x00
FLAGS = ENCRYPTED_FLAG | RESPONSE_FLAG


@dataclass(init=False)
class Frame:
    """One A2687 frame; ``raw_command`` keeps the flag bits."""

    _group: int
    _raw_command: int
    _payload: bytes
    _speaker: int
    _version: int

    def __init__(self, group: int, raw_command: int, payload: bytes, *,
                 speaker: int = APP_SPEAKER,
                 version: int = PROTOCOL_VERSION):
        self._group = group
        self._raw_command = raw_command
        self._payload = payload
        self._speaker = speaker
        self._version = version

    @property
    def group(self) -> int:
        return self._group

    @property
    def raw_command(self) -> int:
        return self._raw_command

    @property
    def payload(self) -> bytes:
        return self._payload

    @property
    def speaker(self) -> int:
        return self._speaker

    @property
    def version(self) -> int:
        return self._version

    @property
    def opcode(self) -> int:
        """Command without the flags, e.g. wire 0x4A07 -> 0x0207."""

        return self._raw_command & ~FLAGS

    @property
    def encrypted(self) -> bool:
        return bool(self._raw_command & ENCRYPTED_FLAG)

    @property
    def response(self) -> bool:
        return bool(self._raw_command & RESPONSE_FLAG)

    @property
    def from_device(self) -> bool:
        return self._speaker != APP_SPEAKER or self.response

    @classmethod
    def build(cls, group: int, opcode: int, payload: bytes, *,
              encrypted: bool, response: bool = False) -> Self:
        raw = opcode
        if encrypted:
            raw |= ENCRYPTED_FLAG
        if response:
            raw |= RESPONSE_FLAG
        return cls(group, raw, payload)


class FrameReassembler(ff09.FrameReassembler[Frame]):
    """Reassembler producing A2687 frames from BLE notifications."""

    def __init__(self) -> None:
        super().__init__(decode_frame)


def decode_frame(data: bytes) -> Frame:
    """Decode one complete frame; raises ``FrameError`` on any defect."""

    validate_envelope(data)
    return Frame(group=data[6], speaker=data[5], version=data[4],
                 raw_command=int.from_bytes(data[7:9], 'big'),
                 payload=bytes(data[PAYLOAD_OFFSET:-1]))


def encode_frame(frame: Frame) -> bytes:
    body = bytearray(HEADER + b'\x00\x00')
    body += bytes((frame.version, frame.speaker, frame.group))
    body += frame.raw_command.to_bytes(2, 'big')
    body += frame.payload
    return seal_frame(body)
