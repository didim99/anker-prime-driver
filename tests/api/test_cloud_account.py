"""Cloud account factory tests with fake HTTP and MQTT."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from ankerprime.api import AccountOptions, CloudAccount
from ankerprime.cloud.auth import AccountCredentials, Authentication
from ankerprime.cloud.client import BoundDevice
from ankerprime.cloud.regions import CN_SERVER, EU_SERVER
from ankerprime.device.a2345 import A2345Timings
from ankerprime.model import (
    AuthenticationError, Capability, OutputId, ProductCode, UnknownProductError)
from tests.cloud.fakes import SMS_CODE, FakeAnkerCloud, FakeHttpClient
from tests.protocol.a2345.fixtures import STATUS_SNAPSHOT
from tests.transport.fakes import FakeMqttConnector
from tests.utils import no_tls, wait_until


NOW = datetime.fromtimestamp(1_900_000_000, UTC)
CREDENTIALS = AccountCredentials('a@example.com', 'secret', 'JP')
TIMINGS = A2345Timings(realtime_refresh=0.05, stale_after=0.5,
                       telemetry_deadline=2.0, readback_delay=0.02,
                       retry_initial=0.02, retry_max=0.05, tick=0.01)


async def make_account(server: FakeAnkerCloud,
                       connector: FakeMqttConnector | None = None,
                       keep_credentials: bool = True,
                       on_authentication: Callable[[Authentication], None]
                       | None = None) -> CloudAccount:
    http = FakeHttpClient(server)
    options = AccountOptions(connector=connector or FakeMqttConnector(),
                             tls_factory=no_tls,
                             on_authentication=on_authentication,
                             timings=TIMINGS, clock=lambda: NOW)
    return await CloudAccount.login(http, CREDENTIALS,
                                    keep_credentials=keep_credentials,
                                    options=options)


async def test_login_and_device_listing() -> None:
    server = FakeAnkerCloud()
    account = await make_account(server)
    assert account.authentication.auth_token == 'token-1'
    assert account.authentication.server == EU_SERVER
    devices = await account.bound_devices()
    assert [device.serial for device in devices] == ['SERIAL123', 'SERIAL456']


async def test_chargers_are_cloud_products_only_and_cached() -> None:
    account = await make_account(FakeAnkerCloud())
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
    server = FakeAnkerCloud()
    seen: list[Authentication] = []
    account = await make_account(server, on_authentication=seen.append)
    server.revoke('token-1')
    devices = await account.bound_devices()
    assert len(devices) == 2
    assert account.authentication.auth_token == 'token-2'
    assert [auth.auth_token for auth in seen] == ['token-2']


async def test_without_credentials_rejection_reaches_caller() -> None:
    server = FakeAnkerCloud()
    account = await make_account(server, keep_credentials=False)
    server.revoke('token-1')
    with pytest.raises(AuthenticationError):
        await account.bound_devices()
    assert server.tokens == ['token-1']


async def test_failed_relogin_raises() -> None:
    server = FakeAnkerCloud()
    account = await make_account(server)
    server.revoke('token-1')
    server.set_login_ok(False)
    with pytest.raises(AuthenticationError, match='wrong password'):
        await account.bound_devices()


async def test_charger_end_to_end_over_fake_mqtt() -> None:
    server = FakeAnkerCloud()
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


async def test_charger_reads_account_settings_with_relogin() -> None:
    server = FakeAnkerCloud()
    connector = FakeMqttConnector()
    account = await make_account(server, connector)
    charger = (await account.chargers())[0]
    server.mini_power.settings('SERIAL123')['charging_mode_status'] = 1
    server.revoke('token-1')
    try:
        await charger.connect()
        connector.last.deliver('dt/anker_power/A2345/SERIAL123/res',
                               STATUS_SNAPSHOT)
        await wait_until(lambda: charger.state.settings.custom_profiles
                         is not None)
        assert charger.supports(Capability.CUSTOM_PROFILES)
        assert charger.state.settings.custom_mode_enabled is True
        assert account.authentication.auth_token == 'token-2'
        names = [name for name, _ in server.mini_power.calls]
        assert names[-2:] == ['get_charging_device_identity_status_default_true',
                              'get_charging_mode_list']
    finally:
        await account.close()


async def test_phone_login_uses_the_mainland_server() -> None:
    server = FakeAnkerCloud()
    http = FakeHttpClient(server)
    options = AccountOptions(connector=FakeMqttConnector(),
                             tls_factory=no_tls, timings=TIMINGS,
                             clock=lambda: NOW)
    account = await CloudAccount.login_phone(http, '13800138000', SMS_CODE,
                                             options=options)
    assert account.authentication.server == CN_SERVER
    devices = await account.bound_devices()
    assert len(devices) == 2
    request = http.requests[-1]
    assert request.host == 'aiot-api-cn.anker.com.cn'
    assert request.headers['model-type'] == 'PHONE'
    # No password: a revoked token cannot be replaced automatically.
    server.revoke(account.authentication.auth_token)
    with pytest.raises(AuthenticationError):
        await account.bound_devices()


async def test_phone_login_with_a_wrong_code() -> None:
    http = FakeHttpClient(FakeAnkerCloud())
    with pytest.raises(AuthenticationError):
        await CloudAccount.login_phone(
            http, '13800138000', '000000',
            options=AccountOptions(clock=lambda: NOW))


def test_options_replace_keeps_the_rest() -> None:
    connector = FakeMqttConnector()
    options = AccountOptions(connector=connector, timings=TIMINGS)
    changed = options.replace(clock=lambda: NOW)
    assert changed.clock() == NOW
    assert changed.connector is connector and changed.timings is TIMINGS
    assert options.clock is not changed.clock
    with pytest.raises(TypeError):
        options.replace(unknown=1)
