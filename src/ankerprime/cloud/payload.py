"""MQTT payload wrapping of A2345 frames on the cloud path.

Inbound messages carry the FF09 frame raw or base64-encoded under ``data`` or
``payload`` keys, possibly inside JSON nested in a string. Outbound commands
use the official app's JSON envelope (cmd 17) for reads and writes alike.
"""

from __future__ import annotations

import base64
import binascii
import json
from enum import StrEnum
from typing import Any

from ankerprime.cloud.client import BoundDevice, MqttCredentials
from ankerprime.model.errors import InvalidParameterError, ProtocolError
from ankerprime.protocol.a2345.frame import decode_frame
from ankerprime.protocol.ff09 import HEADER, MAX_FRAME_LENGTH, FrameError


MAX_INPUT_BYTES = 256 * 1024
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 512
MAX_BASE64_CHARACTERS = (MAX_FRAME_LENGTH + 2) // 3 * 4
CANDIDATE_KEYS = frozenset({'data', 'payload'})

ENVELOPE_VERSION = '1.0.0.1'
ENVELOPE_COMMAND = 17
ENVELOPE_COMMAND_STATUS = 2
DEFAULT_SESSION_ID = '1234-5678'
MAX_ID_LENGTH = 1024


class PayloadErrorKind(StrEnum):
    """Reason no frame could be taken from an MQTT payload."""

    EMPTY = 'empty'
    INPUT_TOO_LARGE = 'input_too_large'
    CANDIDATE_TOO_LARGE = 'candidate_too_large'
    MALFORMED_JSON = 'malformed_json'
    MAX_DEPTH_EXCEEDED = 'max_depth_exceeded'
    TOO_MANY_NODES = 'too_many_nodes'
    INVALID_BASE64 = 'invalid_base64'
    NOT_FF09 = 'not_ff09'
    INVALID_FRAME = 'invalid_frame'
    FRAME_NOT_FOUND = 'frame_not_found'
    AMBIGUOUS_FRAMES = 'ambiguous_frames'


# Which candidate failure explains a payload best when no frame was found.
ERROR_PRIORITY = (PayloadErrorKind.CANDIDATE_TOO_LARGE,
                  PayloadErrorKind.MALFORMED_JSON,
                  PayloadErrorKind.INVALID_BASE64, PayloadErrorKind.NOT_FF09,
                  PayloadErrorKind.INVALID_FRAME)


class PayloadError(ProtocolError):
    """An MQTT payload does not carry exactly one valid frame.

    The message never contains payload bytes: cloud messages carry account
    and device identifiers.
    """

    _kind: PayloadErrorKind

    def __init__(self, kind: PayloadErrorKind):
        super().__init__(f'unusable A2345 cloud payload ({kind})')
        self._kind = kind

    @property
    def kind(self) -> PayloadErrorKind:
        return self._kind


class _FrameSearch:
    """Walks decoded JSON collecting frames and candidate failures."""

    _frames: set[bytes]
    _errors: set[PayloadErrorKind]
    _visited: int

    def __init__(self) -> None:
        self._frames = set()
        self._errors = set()
        self._visited = 0

    def result(self) -> bytes:
        if len(self._frames) > 1:
            raise PayloadError(PayloadErrorKind.AMBIGUOUS_FRAMES)
        if self._frames:
            return next(iter(self._frames))
        for kind in ERROR_PRIORITY:
            if kind in self._errors:
                raise PayloadError(kind)
        raise PayloadError(PayloadErrorKind.FRAME_NOT_FOUND)

    def walk(self, value: Any, depth: int, inspect_string: bool) -> None:
        if depth > MAX_JSON_DEPTH:
            raise PayloadError(PayloadErrorKind.MAX_DEPTH_EXCEEDED)
        self._visited += 1
        if self._visited > MAX_JSON_NODES:
            raise PayloadError(PayloadErrorKind.TOO_MANY_NODES)
        if isinstance(value, dict):
            for key, child in value.items():
                candidate = str(key).lower() in CANDIDATE_KEYS
                if candidate and isinstance(child, str):
                    self._inspect(child, depth + 1)
                else:
                    self.walk(child, depth + 1, False)
        elif isinstance(value, list):
            for child in value:
                self.walk(child, depth + 1, False)
        elif inspect_string and isinstance(value, str):
            self._inspect(value, depth + 1)

    def _inspect(self, text: str, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise PayloadError(PayloadErrorKind.MAX_DEPTH_EXCEEDED)
        if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
            self._errors.add(PayloadErrorKind.CANDIDATE_TOO_LARGE)
            return
        stripped = text.strip()
        if stripped[:1] in ('{', '['):
            try:
                nested = json.loads(stripped)
            except ValueError:
                self._errors.add(PayloadErrorKind.MALFORMED_JSON)
                return
            self.walk(nested, depth, True)
            return
        self._inspect_base64(text)

    def _inspect_base64(self, text: str) -> None:
        if len(text) > MAX_BASE64_CHARACTERS:
            self._errors.add(PayloadErrorKind.CANDIDATE_TOO_LARGE)
            return
        try:
            data = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            self._errors.add(PayloadErrorKind.INVALID_BASE64)
            return
        if not data.startswith(HEADER):
            self._errors.add(PayloadErrorKind.NOT_FF09)
            return
        if len(data) > MAX_FRAME_LENGTH:
            self._errors.add(PayloadErrorKind.CANDIDATE_TOO_LARGE)
            return
        try:
            decode_frame(data)
        except FrameError:
            self._errors.add(PayloadErrorKind.INVALID_FRAME)
            return
        self._frames.add(data)


class CommandEnvelope:
    """Builds MQTT command payloads for one charger."""

    _client_id: str
    _account_id: str
    _product_code: str
    _serial: str

    def __init__(self, credentials: MqttCredentials, device: BoundDevice):
        account_id = device.owner_user_id or credentials.user_id
        ids = (credentials.user_id, credentials.certificate_id, account_id)
        if any(not value or len(value) > MAX_ID_LENGTH for value in ids):
            raise InvalidParameterError('invalid MQTT identity')
        self._client_id = '-'.join(('android', credentials.app_name,
                                    credentials.user_id,
                                    credentials.certificate_id))
        self._account_id = account_id
        self._product_code = device.product_code
        self._serial = device.serial

    def build(self, frame: bytes, timestamp: int,
              session_id: str = DEFAULT_SESSION_ID) -> bytes:
        """Wrap an encoded request frame; compact JSON with sorted keys."""

        if not 0 <= timestamp <= 0xFFFFFFFF or not session_id:
            raise InvalidParameterError('invalid envelope timestamp or session')
        payload = {'account_id': self._account_id,
                   'data': base64.b64encode(frame).decode('ascii'),
                   'device_sn': self._serial}
        head = {'client_id': self._client_id, 'cmd': ENVELOPE_COMMAND,
                'cmd_status': ENVELOPE_COMMAND_STATUS,
                'device_pn': self._product_code, 'device_sn': self._serial,
                'msg_seq': 1, 'seed': 1, 'sess_id': session_id,
                'sign_code': 1, 'timestamp': timestamp,
                'version': ENVELOPE_VERSION}
        envelope = {'head': head, 'payload': _compact_json(payload)}
        return _compact_json(envelope).encode('utf-8')


def extract_frame(payload: bytes) -> bytes:
    """Return the one valid FF09 frame an MQTT payload carries.

    Duplicate copies of the same frame are accepted; distinct frames are
    rejected rather than resolved by key order.
    """

    if not payload:
        raise PayloadError(PayloadErrorKind.EMPTY)
    if len(payload) > MAX_INPUT_BYTES:
        raise PayloadError(PayloadErrorKind.INPUT_TOO_LARGE)
    if payload.startswith(HEADER):
        return _validated_raw_frame(payload)
    if payload.lstrip()[:1] not in (b'{', b'['):
        raise PayloadError(PayloadErrorKind.NOT_FF09)
    try:
        root = json.loads(payload)
    except ValueError:
        raise PayloadError(PayloadErrorKind.MALFORMED_JSON) from None
    search = _FrameSearch()
    search.walk(root, 0, False)
    return search.result()


def topic_belongs_to(topic: str, subscription: str) -> bool:
    """Check that an inbound topic is under a ``.../#`` subscription root.

    The separator is part of the check so ``SERIAL1234`` never matches a
    subscription for ``SERIAL123``.
    """

    if not subscription.endswith('/#'):
        return False
    root = subscription[:-2]
    return topic == root or topic.startswith(f'{root}/')


def _validated_raw_frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME_LENGTH:
        raise PayloadError(PayloadErrorKind.CANDIDATE_TOO_LARGE)
    try:
        decode_frame(payload)
    except FrameError:
        raise PayloadError(PayloadErrorKind.INVALID_FRAME) from None
    return payload


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), sort_keys=True,
                      ensure_ascii=False)
