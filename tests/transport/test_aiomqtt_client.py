"""aiomqtt adapter tests against a local amqtt broker."""

import asyncio
import base64
import socket
import sys
from collections.abc import AsyncIterator

import pytest

from ankerprime.interfaces.mqtt import MqttConnection, MqttEndpoint
from ankerprime.model import ChargerConnectionError, InvalidParameterError
from ankerprime.model.proxy import ProxyAddress
from ankerprime.transport.aiomqtt_client import AiomqttConnector
from tests.transport.proxy import running_proxy


broker_module = pytest.importorskip('amqtt.broker')


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
async def broker_port() -> AsyncIterator[int]:
    port = free_port()
    config = {'listeners': {'default': {'type': 'tcp',
                                        'bind': f'127.0.0.1:{port}'}},
              'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin':
                          {'allow_anonymous': True}}}
    broker = broker_module.Broker(config)
    await broker.start()
    yield port
    await broker.shutdown()


async def connect(port: int, client_id: str) -> MqttConnection:
    endpoint = MqttEndpoint('127.0.0.1', port, client_id, None, 30)
    return await AiomqttConnector(timeout=5).connect(endpoint)


async def test_subscribe_publish_receive(broker_port: int) -> None:
    receiver = await connect(broker_port, 'receiver')
    sender = await connect(broker_port, 'sender')
    try:
        await receiver.subscribe('dt/anker_power/A2345/SERIAL/#')
        await sender.publish('dt/anker_power/A2345/SERIAL/res', b'\xff\x09')
        async with asyncio.timeout(5):
            async for message in receiver.messages():
                assert message.topic == 'dt/anker_power/A2345/SERIAL/res'
                assert message.payload == b'\xff\x09'
                break
        await receiver.unsubscribe('dt/anker_power/A2345/SERIAL/#')
    finally:
        await sender.close()
        await receiver.close()
        await receiver.close()


async def test_connect_failure_raises_connection_error() -> None:
    with pytest.raises(ChargerConnectionError):
        await connect(free_port(), 'nobody')


async def test_use_after_close_raises_connection_error(
        broker_port: int) -> None:
    connection = await connect(broker_port, 'closer')
    await connection.close()
    with pytest.raises(ChargerConnectionError):
        await connection.publish('topic', b'x')


async def test_connect_through_a_proxy(broker_port: int) -> None:
    pytest.importorskip('socks')
    async with running_proxy() as proxy:
        address = ProxyAddress('127.0.0.1', proxy.port, username='u',
                               password='p:w')
        endpoint = MqttEndpoint('127.0.0.1', broker_port, 'proxied', None, 30)
        connection = await AiomqttConnector(timeout=5, proxy=address).connect(
            endpoint)
        try:
            await connection.subscribe('dt/anker_power/A2345/SERIAL/#')
        finally:
            await connection.close()
    assert proxy.requests == [f'CONNECT 127.0.0.1:{broker_port} HTTP/1.1']
    credentials = base64.b64encode(b'u:p:w').decode()
    assert [value.lower() if value else value
            for value in proxy.authorizations] == [f'basic {credentials}'.lower()]


def test_proxy_needs_pysocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, 'socks', None)
    with pytest.raises(InvalidParameterError, match='proxy" extra'):
        AiomqttConnector(proxy=ProxyAddress('127.0.0.1', 3128))
