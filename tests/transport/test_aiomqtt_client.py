"""aiomqtt adapter tests against a local amqtt broker."""

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest

from ankerprime.interfaces.mqtt import MqttConnection, MqttEndpoint
from ankerprime.model import ChargerConnectionError
from ankerprime.transport.aiomqtt_client import AiomqttConnector


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
