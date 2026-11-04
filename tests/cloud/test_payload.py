"""Cloud MQTT payload extraction and command envelope tests."""

import base64
import json
from typing import Any

import pytest

from ankerprime.cloud.client import BoundDevice, MqttCredentials
from ankerprime.cloud.payload import (
    MAX_BASE64_CHARACTERS, MAX_INPUT_BYTES, MAX_JSON_DEPTH, CommandEnvelope,
    PayloadError, PayloadErrorKind, extract_frame, topic_belongs_to)
from ankerprime.model import InvalidParameterError, OutputId
from ankerprime.protocol.a2345 import commands
from tests.protocol.a2345.fixtures import (
    REALTIME_ACKNOWLEDGEMENT, REQUEST_EPOCH, VERSION_INFO)


ACK_BASE64 = base64.b64encode(REALTIME_ACKNOWLEDGEMENT).decode()


def as_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def payload_error(payload: bytes) -> PayloadErrorKind:
    with pytest.raises(PayloadError) as exc_info:
        extract_frame(payload)
    return exc_info.value.kind


def make_envelope(owner: str | None = 'owner') -> CommandEnvelope:
    credentials = MqttCredentials(user_id='user', app_name='anker_power',
                                  thing_name='thing',
                                  certificate_id='certificate',
                                  endpoint='aiot-mqtt-eu.anker.com',
                                  certificate_pem='', private_key_pem='')
    device = BoundDevice('SERIAL123', 'A2345', owner_user_id=owner)
    return CommandEnvelope(credentials, device)


def test_raw_frame_is_returned_unchanged() -> None:
    assert extract_frame(REALTIME_ACKNOWLEDGEMENT) == REALTIME_ACKNOWLEDGEMENT


def test_payload_string_with_nested_json() -> None:
    nested = json.dumps({'data': ACK_BASE64})
    assert extract_frame(as_json({'payload': nested})) == REALTIME_ACKNOWLEDGEMENT


def test_frame_under_nested_data_or_payload_keys() -> None:
    document = {'metadata': {'ignored': True},
                'items': [{'wrapper': {'payload': ACK_BASE64}}]}
    assert extract_frame(as_json(document)) == REALTIME_ACKNOWLEDGEMENT


def test_duplicate_copies_of_one_frame_are_accepted() -> None:
    document = {'data': ACK_BASE64, 'nested': [{'payload': ACK_BASE64}]}
    assert extract_frame(as_json(document)) == REALTIME_ACKNOWLEDGEMENT


def test_valid_frame_wins_over_unrelated_data_sibling() -> None:
    document = {'data': 'ordinary-envelope-metadata',
                'message': {'payload': ACK_BASE64}}
    assert extract_frame(as_json(document)) == REALTIME_ACKNOWLEDGEMENT


def test_distinct_frames_are_ambiguous() -> None:
    document = {'data': ACK_BASE64,
                'nested': {'payload': base64.b64encode(VERSION_INFO).decode()}}
    assert payload_error(as_json(document)) == PayloadErrorKind.AMBIGUOUS_FRAMES


@pytest.mark.parametrize(('payload', 'kind'), [
    (as_json({'data': '%%%not-base64%%%'}), PayloadErrorKind.INVALID_BASE64),
    (as_json({'payload': base64.b64encode(b'ordinary text').decode()}),
     PayloadErrorKind.NOT_FF09),
    (b'\x01\x02\x03', PayloadErrorKind.NOT_FF09),
    (REALTIME_ACKNOWLEDGEMENT[:-1] + b'\x00', PayloadErrorKind.INVALID_FRAME),
    (b'{"data": ', PayloadErrorKind.MALFORMED_JSON),
    (as_json({'payload': '  {not-json}'}), PayloadErrorKind.MALFORMED_JSON),
    (b'', PayloadErrorKind.EMPTY),
    (as_json({'message': {'value': 'ignored'}}),
     PayloadErrorKind.FRAME_NOT_FOUND),
    (b' ' * (MAX_INPUT_BYTES + 1), PayloadErrorKind.INPUT_TOO_LARGE),
    (as_json({'data': 'A' * (MAX_BASE64_CHARACTERS + 1)}),
     PayloadErrorKind.CANDIDATE_TOO_LARGE),
])
def test_rejected_payloads(payload: bytes, kind: PayloadErrorKind) -> None:
    assert payload_error(payload) == kind


def test_json_beyond_maximum_depth_is_rejected() -> None:
    document: Any = {'data': ACK_BASE64}
    for _ in range(MAX_JSON_DEPTH + 1):
        document = {'nested': document}
    assert payload_error(as_json(document)) == (
        PayloadErrorKind.MAX_DEPTH_EXCEEDED)


def test_error_message_does_not_leak_payload() -> None:
    with pytest.raises(PayloadError) as exc_info:
        extract_frame(as_json({'data': 'SERIAL-SECRET'}))
    assert 'SERIAL-SECRET' not in str(exc_info.value)


def test_read_envelope_is_exact_compact_json() -> None:
    frame = commands.status_snapshot().encode(REQUEST_EPOCH)
    data = make_envelope().build(frame, REQUEST_EPOCH)
    assert data.decode() == (
        '{"head":{"client_id":"android-anker_power-user-certificate",'
        '"cmd":17,"cmd_status":2,"device_pn":"A2345","device_sn":"SERIAL123",'
        '"msg_seq":1,"seed":1,"sess_id":"1234-5678","sign_code":1,'
        '"timestamp":1700000000,"version":"1.0.0.1"},'
        '"payload":"{\\"account_id\\":\\"owner\\",'
        '\\"data\\":\\"/wkUAAMADwIAoQEi/gUDAPFTZVE=\\",'
        '\\"device_sn\\":\\"SERIAL123\\"}"}')


def test_write_envelope_carries_the_command_frame() -> None:
    frame = commands.set_output(OutputId.C1, True).encode(REQUEST_EPOCH)
    data = make_envelope().build(frame, REQUEST_EPOCH)
    envelope = json.loads(data)
    assert envelope['head']['cmd'] == 17
    payload = json.loads(envelope['payload'])
    assert base64.b64decode(payload['data']).hex() == (
        'ff091c0003000f0207a10122a2020100a3020101fe050300f153655e')


def test_account_id_falls_back_to_credentials_user() -> None:
    frame = commands.status_snapshot().encode(REQUEST_EPOCH)
    data = make_envelope(owner=None).build(frame, REQUEST_EPOCH)
    payload = json.loads(json.loads(data)['payload'])
    assert payload['account_id'] == 'user'


def test_envelope_rejects_invalid_timestamp() -> None:
    with pytest.raises(InvalidParameterError):
        make_envelope().build(b'', -1)


@pytest.mark.parametrize(('topic', 'accepted'), [
    ('dt/anker_power/A2345/SERIAL123', True),
    ('dt/anker_power/A2345/SERIAL123/res', True),
    ('dt/anker_power/A2345/SERIAL1234/res', False),
    ('dt/anker_power/A2345/SERIAL12', False),
])
def test_inbound_topic_must_belong_to_device(topic: str,
                                             accepted: bool) -> None:
    subscription = 'dt/anker_power/A2345/SERIAL123/#'
    assert topic_belongs_to(topic, subscription) is accepted
    assert not topic_belongs_to(topic, 'dt/anker_power/A2345/SERIAL123')
