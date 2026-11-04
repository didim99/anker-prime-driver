"""Authenticated cloud API tests."""

import hashlib
from datetime import UTC, datetime

import pytest

from ankerprime.cloud.auth import Authentication
from ankerprime.cloud.client import (
    CERTIFICATE_LABELS, BoundDevice,
    CloudClient, MqttCredentials, normalize_pem)
from ankerprime.cloud.regions import EU_SERVER
from ankerprime.interfaces.http import HttpResponse
from ankerprime.model import (
    AuthenticationError, ChargerConnectionError, ProtocolError)
from tests.cloud.fakes import FakeHttpClient, json_response


USER_ID = 'a' * 40
NOW = datetime.fromtimestamp(1_900_000_000, UTC)

CERTIFICATE = '-----BEGIN CERTIFICATE-----\nAQIDBA==\n-----END CERTIFICATE-----'
PRIVATE_KEY = ('-----BEGIN RSA PRIVATE KEY-----\nBQYHCA==\n'
               '-----END RSA PRIVATE KEY-----')
MQTT_INFO = {
    'user_id': USER_ID, 'app_name': 'anker_power', 'thing_name': 'thing-name',
    'certificate_id': 'certificate-id', 'endpoint_addr': 'aiot-mqtt-eu.anker.com',
    'certificate_pem': CERTIFICATE, 'private_key': PRIVATE_KEY,
}


def make_auth(expiry: int = 2_000_000_000) -> Authentication:
    return Authentication(user_id=USER_ID, auth_token='test-token',
                          expires_at=datetime.fromtimestamp(expiry, UTC),
                          country='JP', server=EU_SERVER)


def make_client(http: FakeHttpClient, expiry: int = 2_000_000_000
                ) -> CloudClient:
    return CloudClient(http, make_auth(expiry), lambda: NOW)


def make_credentials() -> MqttCredentials:
    return MqttCredentials(user_id='user', app_name='anker_power',
                           thing_name='thing', certificate_id='certificate',
                           endpoint='aiot-mqtt-eu.anker.com',
                           certificate_pem=CERTIFICATE,
                           private_key_pem=PRIVATE_KEY)


async def test_bound_devices_use_authenticated_headers_and_filter() -> None:
    devices = [
        {'device_sn': 'SERIALA2345', 'product_code': 'A2345',
         'device_name': 'Prime Charger', 'alias_name': 'Desk charger',
         'wifi_online': True, 'device_sw_version': '2.1.1.6'},
        {'device_sn': 'SERIALA2687', 'product_code': 'A2687'},
        {'product_code': 'A2345'},
    ]
    http = FakeHttpClient(lambda _: json_response(
        {'code': 0, 'data': {'data': devices}}))
    found = await make_client(http).bound_devices('a2345')

    request = http.requests[0]
    assert request.url == (EU_SERVER + '/power_service/v1/app/'
                           'get_relate_and_bind_devices')
    assert request.headers['x-auth-token'] == 'test-token'
    expected_gtoken = hashlib.md5(USER_ID.encode()).hexdigest()
    assert request.headers['gtoken'] == expected_gtoken
    assert request.headers['country'] == 'JP'
    assert request.body == b'{}'

    assert len(found) == 1
    device = found[0]
    assert device.serial == 'SERIALA2345'
    assert device.display_name == 'Desk charger'
    assert device.wifi_online is True
    assert device.software_version == '2.1.1.6'


async def test_bound_devices_without_filter() -> None:
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': {
        'data': [{'device_sn': 'S1', 'product_code': 'A2687'}]}}))
    found = await make_client(http).bound_devices()
    assert [device.display_name for device in found] == ['A2687']


async def test_mqtt_credentials_and_topics() -> None:
    http = FakeHttpClient(lambda _: json_response(
        {'code': 0, 'data': MQTT_INFO}))
    credentials = await make_client(http).mqtt_credentials()
    assert http.requests[0].path == '/app/devicemanage/get_user_mqtt_info'
    assert credentials.endpoint == 'aiot-mqtt-eu.anker.com'
    assert credentials.certificate_pem == (
        '-----BEGIN CERTIFICATE-----\nAQIDBA==\n-----END CERTIFICATE-----\n')
    assert 'RSA PRIVATE KEY' in credentials.private_key_pem
    assert credentials.client_id(42) == 'thing-name_00042'
    device = BoundDevice('SERIAL123', 'A2345')
    assert credentials.subscription_topic(device) == (
        'dt/anker_power/A2345/SERIAL123/#')
    assert credentials.command_topic(device) == (
        'cmd/anker_power/A2345/SERIAL123/req')
    assert 'BQYHCA' not in repr(credentials)


@pytest.mark.parametrize('change', [
    {'certificate_pem': 'not a certificate'},
    {'private_key': 'not a private key'},
    {'endpoint_addr': 'evil.example/path'},
    {'thing_name': ''},
])
async def test_malformed_mqtt_credentials_fail_closed(
        change: dict[str, str]) -> None:
    http = FakeHttpClient(lambda _: json_response(
        {'code': 0, 'data': {**MQTT_INFO, **change}}))
    with pytest.raises(ProtocolError):
        await make_client(http).mqtt_credentials()


def test_topics_reject_wildcards_from_server_identity() -> None:
    credentials = make_credentials()
    with pytest.raises(ProtocolError):
        credentials.subscription_topic(BoundDevice('SERIAL/#', 'A2345'))
    with pytest.raises(ProtocolError):
        credentials.command_topic(BoundDevice('SERIAL/+', 'A2345'))


async def test_expired_token_stops_before_network() -> None:
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': {}}))
    with pytest.raises(AuthenticationError):
        await make_client(http, expiry=100).bound_devices()
    assert not http.requests


@pytest.mark.parametrize('response', [
    HttpResponse(401, b''), HttpResponse(403, b''),
    json_response({'code': 26084, 'msg': 'session expired'}),
    json_response({'code': 401}),
])
async def test_auth_failures_are_normalised(response: HttpResponse) -> None:
    http = FakeHttpClient(lambda _: response)
    with pytest.raises(AuthenticationError):
        await make_client(http).bound_devices()


async def test_other_failures() -> None:
    http = FakeHttpClient(lambda _: HttpResponse(500, b''))
    with pytest.raises(ChargerConnectionError):
        await make_client(http).bound_devices()
    http = FakeHttpClient(lambda _: json_response({'code': 7, 'msg': 'nope'}))
    with pytest.raises(ProtocolError, match='nope'):
        await make_client(http).bound_devices()
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': None}))
    with pytest.raises(ProtocolError):
        await make_client(http).bound_devices()


@pytest.mark.parametrize('text', [
    'garbage\n-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----',
    '-----BEGIN CERTIFICATE-----\n\n-----END CERTIFICATE-----',
    '-----BEGIN CERTIFICATE-----\n%%%\n-----END CERTIFICATE-----',
    '-----END CERTIFICATE-----\nAQID\n-----BEGIN CERTIFICATE-----',
])
def test_normalize_pem_rejects_malformed_blocks(text: str) -> None:
    assert normalize_pem(text, CERTIFICATE_LABELS) is None


def test_normalize_pem_rewraps_long_bodies() -> None:
    body = 'QUJD' * 40
    text = f'-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----'
    pem = normalize_pem(text, CERTIFICATE_LABELS)
    assert pem is not None
    assert [len(line) for line in pem.splitlines()[1:-1]] == [64, 64, 32]
