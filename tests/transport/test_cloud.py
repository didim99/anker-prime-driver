"""Cloud MQTT session and channel tests with a fake broker connection."""

import base64
import json
from collections.abc import AsyncIterator

import pytest

from ankerprime.cloud.client import BoundDevice, MqttCredentials
from ankerprime.interfaces.channel import ChannelListener
from ankerprime.model import AuthenticationError, ChargerConnectionError
from ankerprime.protocol.a2345 import commands
from ankerprime.transport.cloud import CloudMqttSession
from tests.protocol.a2345.fixtures import (
    REALTIME_ACKNOWLEDGEMENT, REQUEST_EPOCH, VERSION_INFO)
from tests.transport.fakes import FakeMqttConnector
from tests.utils import make_mqtt_credentials, no_tls, wait_until


DEVICE_A = BoundDevice('SERIAL123', 'A2345', owner_user_id='owner')
DEVICE_B = BoundDevice('SERIAL456', 'A2345')
TOPIC_A = 'dt/anker_power/A2345/SERIAL123/#'
TOPIC_B = 'dt/anker_power/A2345/SERIAL456/#'


class Recorder(ChannelListener):
    """Collects what a channel reports."""

    _messages: list[bytes]
    _closures: list[Exception | None]

    def __init__(self) -> None:
        self._messages = []
        self._closures = []

    @property
    def messages(self) -> list[bytes]:
        return self._messages

    @property
    def closures(self) -> list[Exception | None]:
        return self._closures

    def on_message(self, data: bytes) -> None:
        self._messages.append(data)

    def on_closed(self, error: Exception | None) -> None:
        self._closures.append(error)


class CredentialSource:
    """Counts fetches; can be told to fail."""

    _fetches: int
    _error: Exception | None

    def __init__(self) -> None:
        self._fetches = 0
        self._error = None

    @property
    def fetches(self) -> int:
        return self._fetches

    def fail_with(self, error: Exception | None) -> None:
        self._error = error

    async def __call__(self) -> MqttCredentials:
        self._fetches += 1
        if self._error is not None:
            raise self._error
        return make_mqtt_credentials()


@pytest.fixture
def connector() -> FakeMqttConnector:
    return FakeMqttConnector()


@pytest.fixture
def source() -> CredentialSource:
    return CredentialSource()


@pytest.fixture
async def session(connector: FakeMqttConnector, source: CredentialSource
                  ) -> AsyncIterator[CloudMqttSession]:
    session = CloudMqttSession(source, connector, tls_factory=no_tls)
    yield session
    await session.close()


def wrap(frame: bytes) -> bytes:
    """Payload as the cloud delivers it: base64 under a nested data key."""

    inner = json.dumps({'data': base64.b64encode(frame).decode()})
    return json.dumps({'head': {'cmd': 17}, 'payload': inner}).encode()


async def test_open_connects_and_subscribes(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    channel = session.channel(DEVICE_A)
    await channel.open()
    assert channel.is_open and session.connected
    endpoint = connector.endpoints[0]
    assert (endpoint.host, endpoint.port, endpoint.keepalive) == (
        'aiot-mqtt-eu.anker.com', 8883, 60)
    assert endpoint.client_id.startswith('thing_')
    assert len(endpoint.client_id) == len('thing_') + 5
    assert connector.last.subscriptions == [TOPIC_A]
    await channel.open()
    assert len(connector.connections) == 1


async def test_send_publishes_command_envelope(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    channel = session.channel(DEVICE_A)
    await channel.open()
    frame = commands.status_snapshot().encode(REQUEST_EPOCH)
    await channel.send(frame)
    topic, payload = connector.last.published[0]
    assert topic == 'cmd/anker_power/A2345/SERIAL123/req'
    envelope = json.loads(payload)
    inner = json.loads(envelope['payload'])
    assert inner['account_id'] == 'owner'
    assert base64.b64decode(inner['data']) == frame


async def test_send_on_closed_channel_raises(session: CloudMqttSession) -> None:
    with pytest.raises(ChargerConnectionError):
        await session.channel(DEVICE_A).send(b'')


async def test_inbound_frames_are_routed_by_topic(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    first, second = session.channel(DEVICE_A), session.channel(DEVICE_B)
    recorder_a, recorder_b = Recorder(), Recorder()
    first.set_listener(recorder_a)
    second.set_listener(recorder_b)
    await first.open()
    await second.open()
    assert len(connector.connections) == 1
    assert connector.last.subscriptions == [TOPIC_A, TOPIC_B]

    connection = connector.last
    connection.deliver('dt/anker_power/A2345/SERIAL123/res',
                       wrap(REALTIME_ACKNOWLEDGEMENT))
    connection.deliver('dt/anker_power/A2345/SERIAL456', VERSION_INFO)
    connection.deliver('dt/anker_power/A2345/SERIAL1234/res', VERSION_INFO)
    connection.deliver('dt/anker_power/A2345/SERIAL123/res', b'{"x": 1}')
    await wait_until(lambda: bool(recorder_a.messages and recorder_b.messages))
    assert recorder_a.messages == [REALTIME_ACKNOWLEDGEMENT]
    assert recorder_b.messages == [VERSION_INFO]


async def test_connection_loss_closes_channels_and_reopen_reconnects(
        session: CloudMqttSession, connector: FakeMqttConnector,
        source: CredentialSource) -> None:
    channel = session.channel(DEVICE_A)
    recorder = Recorder()
    channel.set_listener(recorder)
    await channel.open()
    first = connector.last
    first.drop(ChargerConnectionError('broker went away'))
    await wait_until(lambda: bool(recorder.closures))
    assert isinstance(recorder.closures[0], ChargerConnectionError)
    assert not channel.is_open and not session.connected
    assert first.closed

    await channel.open()
    assert len(connector.connections) == 2
    assert source.fetches == 2
    assert connector.last.subscriptions == [TOPIC_A]


async def test_publish_failure_drops_connection(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    channel = session.channel(DEVICE_A)
    recorder = Recorder()
    channel.set_listener(recorder)
    await channel.open()
    connector.last.set_fail_publish(True)
    with pytest.raises(ChargerConnectionError):
        await channel.send(b'frame')
    assert recorder.closures and not channel.is_open


async def test_last_channel_close_disconnects(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    first, second = session.channel(DEVICE_A), session.channel(DEVICE_B)
    recorder = Recorder()
    first.set_listener(recorder)
    await first.open()
    await second.open()
    await first.close()
    assert recorder.closures == [None]
    assert connector.last.unsubscriptions == [TOPIC_A]
    assert session.connected
    await second.close()
    assert not session.connected and connector.last.closed


async def test_credential_failure_propagates(
        session: CloudMqttSession, source: CredentialSource) -> None:
    source.fail_with(AuthenticationError('token revoked'))
    channel = session.channel(DEVICE_A)
    with pytest.raises(AuthenticationError):
        await channel.open()
    assert not channel.is_open and not session.connected


async def test_connect_failure_propagates(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    connector.fail_next(ChargerConnectionError('refused'))
    channel = session.channel(DEVICE_A)
    with pytest.raises(ChargerConnectionError):
        await channel.open()
    await channel.open()
    assert channel.is_open


async def test_session_close_closes_channels(
        session: CloudMqttSession, connector: FakeMqttConnector) -> None:
    channel = session.channel(DEVICE_A)
    recorder = Recorder()
    channel.set_listener(recorder)
    await channel.open()
    await session.close()
    assert recorder.closures == [None]
    assert not session.connected and connector.last.closed
