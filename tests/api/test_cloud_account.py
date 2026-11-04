"""Cloud account factory tests with fake HTTP and MQTT."""

import base64
import json
import ssl
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from ankerprime.api import CloudAccount
from ankerprime.cloud.auth import AccountCredentials, Authentication
from ankerprime.cloud.client import BoundDevice, MqttCredentials
from ankerprime.cloud.regions import EU_SERVER
from ankerprime.device.a2345 import A2345Timings
from ankerprime.interfaces.http import HttpResponse
from ankerprime.model import (
    AuthenticationError, OutputId, ProductCode, UnknownProductError)
from tests.cloud.fakes import FakeHttpClient, Request, json_response
from tests.device.charger import wait_until
from tests.protocol.a2345.fixtures import STATUS_SNAPSHOT
from tests.transport.fakes import FakeMqttConnector


NOW = datetime.fromtimestamp(1_900_000_000, UTC)
CREDENTIALS = AccountCredentials('a@example.com', 'secret', 'JP')
DEVICES = [
    {'device_sn': 'SERIAL123', 'product_code': 'A2345',
     'alias_name': 'Desk charger', 'device_sw_version': '2.1.1.6'},
    {'device_sn': 'SERIAL456', 'product_code': 'A2687'},
]
MQTT_INFO = {
    'user_id': 'u' * 40, 'app_name': 'anker_power', 'thing_name': 'thing',
    'certificate_id': 'certificate', 'endpoint_addr': 'aiot-mqtt-eu.anker.com',
    'certificate_pem': '-----BEGIN CERTIFICATE-----\nAQID\n'
                       '-----END CERTIFICATE-----',
    'private_key': '-----BEGIN PRIVATE KEY-----\nBAUG\n'
                   '-----END PRIVATE KEY-----',
}
TIMINGS = A2345Timings(realtime_refresh=0.05, stale_after=0.5,
                       telemetry_deadline=2.0, readback_delay=0.02,
                       retry_initial=0.02, retry_max=0.05, tick=0.01)


class CloudServer:
    """Answers the Anker API; tokens can be revoked."""

    _tokens: list[str]
    _revoked: set[str]
    _login_ok: bool

    def __init__(self) -> None:
        self._tokens = []
        self._revoked = set()
        self._login_ok = True

    @property
    def tokens(self) -> list[str]:
        return self._tokens

    def revoke(self, token: str) -> None:
        self._revoked.add(token)

    def set_login_ok(self, ok: bool) -> None:
        self._login_ok = ok

    def __call__(self, request: Request) -> HttpResponse:
        if request.path == '/passport/login':
            return self._login()
        if request.headers.get('x-auth-token') in self._revoked:
            return json_response({'code': 26084, 'msg': 'expired'})
        if request.path.endswith('get_relate_and_bind_devices'):
            return json_response({'code': 0, 'data': {'data': DEVICES}})
        if request.path.endswith('get_user_mqtt_info'):
            return json_response({'code': 0, 'data': MQTT_INFO})
        return HttpResponse(404, b'')

    def _login(self) -> HttpResponse:
        if not self._login_ok:
            return json_response({'code': 100053, 'msg': 'wrong password'})
        token = f'token-{len(self._tokens) + 1}'
        self._tokens.append(token)
        return json_response({'code': 0, 'data': {
            'user_id': 'u' * 40, 'auth_token': token,
            'token_expires_at': 2_000_000_000}})


async def no_tls(credentials: MqttCredentials) -> ssl.SSLContext | None:
    return None


async def make_account(server: CloudServer,
                       connector: FakeMqttConnector | None = None,
                       keep_credentials: bool = True,
                       on_authentication: Callable[[Authentication], None]
                       | None = None) -> CloudAccount:
    http = FakeHttpClient(server)
    return await CloudAccount.login(
        http, CREDENTIALS, keep_credentials=keep_credentials,
        connector=connector or FakeMqttConnector(), tls_factory=no_tls,
        on_authentication=on_authentication, timings=TIMINGS,
        clock=lambda: NOW)


async def test_login_and_device_listing() -> None:
    server = CloudServer()
    account = await make_account(server)
    assert account.authentication.auth_token == 'token-1'
    assert account.authentication.server == EU_SERVER
    devices = await account.bound_devices()
    assert [device.serial for device in devices] == ['SERIAL123', 'SERIAL456']


async def test_chargers_are_cloud_products_only_and_cached() -> None:
    account = await make_account(CloudServer())
    chargers = await account.chargers()
    assert len(chargers) == 1
    charger = chargers[0]
    assert charger.product.code == ProductCode.A2345
    assert charger.state.info.name == 'Desk charger'
    assert charger.state.info.firmware_version == '2.1.1.6'
    again = account.charger(BoundDevice('SERIAL123', 'A2345'))
    assert again is charger
    with pytest.raises(UnknownProductError):
        account.charger(BoundDevice('SERIAL456', 'A2687'))


async def test_rejected_token_triggers_one_relogin() -> None:
    server = CloudServer()
    seen: list[Authentication] = []
    account = await make_account(server, on_authentication=seen.append)
    server.revoke('token-1')
    devices = await account.bound_devices()
    assert len(devices) == 2
    assert account.authentication.auth_token == 'token-2'
    assert [auth.auth_token for auth in seen] == ['token-2']


async def test_without_credentials_rejection_reaches_caller() -> None:
    server = CloudServer()
    account = await make_account(server, keep_credentials=False)
    server.revoke('token-1')
    with pytest.raises(AuthenticationError):
        await account.bound_devices()
    assert server.tokens == ['token-1']


async def test_failed_relogin_raises() -> None:
    server = CloudServer()
    account = await make_account(server)
    server.revoke('token-1')
    server.set_login_ok(False)
    with pytest.raises(AuthenticationError, match='wrong password'):
        await account.bound_devices()


async def test_charger_end_to_end_over_fake_mqtt() -> None:
    server = CloudServer()
    connector = FakeMqttConnector()
    account = await make_account(server, connector)
    charger = (await account.chargers())[0]
    try:
        await charger.connect()
        connection = connector.last
        assert connection.subscriptions == [
            'dt/anker_power/A2345/SERIAL123/#']
        sent = [json.loads(payload) for _, payload in connection.published]
        frames = [base64.b64decode(json.loads(envelope['payload'])['data'])
                  for envelope in sent]
        assert [int.from_bytes(frame[7:9], 'big') for frame in frames] == [
            0x0200, 0x0202, 0x020B]

        connection.deliver('dt/anker_power/A2345/SERIAL123/res',
                           STATUS_SNAPSHOT)
        await wait_until(lambda: charger.state.available)
        assert charger.state.outputs[OutputId.C1].is_on is False
    finally:
        await account.close()
    assert not charger.state.available
    assert connector.last.closed
