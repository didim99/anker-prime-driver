"""``MqttConnector`` on top of aiomqtt (paho-mqtt).

aiomqtt watches the socket with ``loop.add_reader``, so on Windows it needs a
selector event loop, not the default proactor loop. A proxy needs PySocks
(the ``proxy`` extra), which paho-mqtt uses to open the tunnel.
"""

from collections.abc import AsyncIterator
from typing import Any

import aiomqtt

from ankerprime.interfaces.mqtt import (
    MqttConnection, MqttConnector, MqttEndpoint, MqttMessage)
from ankerprime.model.errors import (
    ChargerConnectionError, InvalidParameterError)
from ankerprime.model.proxy import ProxyAddress


DEFAULT_TIMEOUT = 20.0
PROXY_EXTRA_HINT = ('MQTT through a proxy needs the "proxy" extra: '
                    'pip install "anker-prime-driver[proxy]"')


class AiomqttConnection(MqttConnection):
    """One connected aiomqtt client."""

    _client: aiomqtt.Client
    _closed: bool

    def __init__(self, client: aiomqtt.Client):
        self._client = client
        self._closed = False

    async def subscribe(self, topic: str) -> None:
        try:
            await self._client.subscribe(topic, qos=0)
        except aiomqtt.MqttError as error:
            raise _connection_error('subscribe', error) from error

    async def unsubscribe(self, topic: str) -> None:
        try:
            await self._client.unsubscribe(topic)
        except aiomqtt.MqttError as error:
            raise _connection_error('unsubscribe', error) from error

    async def publish(self, topic: str, payload: bytes) -> None:
        try:
            await self._client.publish(topic, payload, qos=0)
        except aiomqtt.MqttError as error:
            raise _connection_error('publish', error) from error

    async def messages(self) -> AsyncIterator[MqttMessage]:
        try:
            async for message in self._client.messages:
                yield MqttMessage(str(message.topic), _as_bytes(message.payload))
        except aiomqtt.MqttError as error:
            raise _connection_error('receive', error) from error

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.__aexit__(None, None, None)
        except aiomqtt.MqttError:
            pass


class AiomqttConnector(MqttConnector):
    """Connects with MQTT 3.1.1 and a clean session, as the official app.

    With a ``proxy`` the broker is reached through an HTTP ``CONNECT``
    tunnel; raises ``InvalidParameterError`` without PySocks.
    """

    _timeout: float
    _proxy: aiomqtt.ProxySettings | None

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, *,
                 proxy: ProxyAddress | None = None):
        self._timeout = timeout
        self._proxy = _proxy_settings(proxy) if proxy is not None else None

    async def connect(self, endpoint: MqttEndpoint) -> MqttConnection:
        client = aiomqtt.Client(endpoint.host, endpoint.port,
                                identifier=endpoint.client_id,
                                protocol=aiomqtt.ProtocolVersion.V311,
                                clean_session=True,
                                keepalive=endpoint.keepalive,
                                tls_context=endpoint.tls,
                                timeout=self._timeout, proxy=self._proxy)
        try:
            await client.__aenter__()
        except (aiomqtt.MqttError, OSError) as error:
            raise _connection_error('connect', error) from error
        return AiomqttConnection(client)


def _proxy_settings(proxy: ProxyAddress) -> aiomqtt.ProxySettings:
    try:
        import socks
    except ImportError:
        raise InvalidParameterError(PROXY_EXTRA_HINT) from None
    return aiomqtt.ProxySettings(
        proxy_type=socks.HTTP, proxy_addr=proxy.host, proxy_port=proxy.port,
        proxy_username=proxy.username, proxy_password=proxy.password)


def _connection_error(action: str, error: Exception) -> ChargerConnectionError:
    return ChargerConnectionError(f'MQTT {action} failed: {error}')


def _as_bytes(payload: Any) -> bytes:
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    if payload is None:
        return b''
    return str(payload).encode('utf-8')
