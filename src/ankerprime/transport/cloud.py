"""Anker cloud MQTT transport: one broker connection per account.

The MQTT client identity belongs to the account, so all its chargers share
one connection; each charger gets a ``CloudChannel`` carrying plain FF09
frames. The session reconnects lazily: a channel reopened after a connection
loss reconnects with freshly fetched credentials.
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from ankerprime.cloud.client import BoundDevice, MqttCredentials
from ankerprime.cloud.credential_files import load_tls_context
from ankerprime.cloud.payload import (
    CommandEnvelope, PayloadError, extract_frame, topic_belongs_to)
from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.interfaces.mqtt import (
    MqttConnection, MqttConnector, MqttEndpoint, MqttMessage)
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.redact import redact


logger = logging.getLogger(__name__)

MQTT_PORT = 8883
KEEPALIVE = 60

CredentialsProvider = Callable[[], Awaitable[MqttCredentials]]
TlsFactory = Callable[[MqttCredentials], Awaitable[ssl.SSLContext | None]]
Clock = Callable[[], datetime]


class CloudChannel(Channel):
    """Frames of one charger over the account's MQTT connection."""

    _session: CloudMqttSession
    _device: BoundDevice

    _listener: ChannelListener | None

    _open: bool
    _subscription_topic: str | None
    _command_topic: str | None
    _envelope: CommandEnvelope | None

    def __init__(self, session: CloudMqttSession, device: BoundDevice):
        self._session = session
        self._device = device
        self._listener = None
        self._open = False
        self._subscription_topic = None
        self._command_topic = None
        self._envelope = None

    @property
    def device(self) -> BoundDevice:
        return self._device

    @property
    def subscription_topic(self) -> str | None:
        return self._subscription_topic

    @property
    def is_open(self) -> bool:
        return self._open

    def set_listener(self, listener: ChannelListener | None) -> None:
        self._listener = listener

    async def open(self) -> None:
        if self._open:
            return
        await self._session.attach(self)

    async def close(self) -> None:
        if not self._open:
            return
        self._open = False
        await self._session.detach(self)
        if self._listener is not None:
            self._listener.on_closed(None)

    async def send(self, data: bytes) -> None:
        envelope = self._envelope
        topic = self._command_topic
        if not self._open or envelope is None or topic is None:
            raise ChargerConnectionError('cloud channel is closed')
        payload = envelope.build(data, self._session.timestamp())
        await self._session.publish(topic, payload)

    def bind(self, credentials: MqttCredentials) -> str:
        """Derive topics and envelope from fresh credentials; mark open."""

        self._subscription_topic = credentials.subscription_topic(self._device)
        self._command_topic = credentials.command_topic(self._device)
        self._envelope = CommandEnvelope(credentials, self._device)
        self._open = True
        return self._subscription_topic

    def unbind(self) -> None:
        self._open = False

    def receive(self, payload: bytes) -> None:
        """Pass the frame of an inbound payload to the listener."""

        if not self._open or self._listener is None:
            return
        try:
            frame = extract_frame(payload)
        except PayloadError as error:
            logger.debug('ignored payload for %s: %s',
                         redact(self._device.serial), error.kind)
            return
        self._listener.on_message(frame)

    def lose(self, error: Exception) -> None:
        """Mark closed after a connection loss and tell the listener."""

        if not self._open:
            return
        self._open = False
        if self._listener is not None:
            self._listener.on_closed(error)


class CloudMqttSession:
    """Owns the account's MQTT connection and routes it to channels."""

    _credentials_provider: CredentialsProvider
    _connector: MqttConnector
    _tls_factory: TlsFactory
    _clock: Clock
    _port: int
    _keepalive: int

    _lock: asyncio.Lock
    _connection: MqttConnection | None
    _credentials: MqttCredentials | None
    _reader: asyncio.Task[None] | None
    _channels: set[CloudChannel]

    def __init__(self, credentials_provider: CredentialsProvider,
                 connector: MqttConnector, *,
                 tls_factory: TlsFactory | None = None,
                 tls_directory: Path | None = None, clock: Clock | None = None,
                 port: int = MQTT_PORT, keepalive: int = KEEPALIVE):
        self._credentials_provider = credentials_provider
        self._connector = connector
        self._tls_factory = tls_factory or _file_tls_factory(tls_directory)
        self._clock = clock or _utc_now
        self._port = port
        self._keepalive = keepalive
        self._lock = asyncio.Lock()
        self._connection = None
        self._credentials = None
        self._reader = None
        self._channels = set()

    @property
    def connected(self) -> bool:
        return self._connection is not None

    def channel(self, device: BoundDevice) -> CloudChannel:
        return CloudChannel(self, device)

    def timestamp(self) -> int:
        return int(self._clock().timestamp())

    async def attach(self, channel: CloudChannel) -> None:
        """Connect if needed and subscribe the channel's topic."""

        async with self._lock:
            if self._connection is None or self._credentials is None:
                connection, credentials = await self._connect()
            else:
                connection, credentials = self._connection, self._credentials
            topic = channel.bind(credentials)
            self._channels.add(channel)
            try:
                await connection.subscribe(topic)
            except ChargerConnectionError as error:
                self._channels.discard(channel)
                channel.unbind()
                await self._drop(connection, error)
                raise

    async def detach(self, channel: CloudChannel) -> None:
        """Unsubscribe; disconnect when no channel is left."""

        async with self._lock:
            self._channels.discard(channel)
            connection = self._connection
            topic = channel.subscription_topic
            if connection is None:
                return
            if self._channels:
                if topic is not None:
                    await self._unsubscribe(connection, topic)
                return
            self._connection = None
            self._stop_reader()
            await connection.close()

    async def publish(self, topic: str, payload: bytes) -> None:
        connection = self._connection
        if connection is None:
            raise ChargerConnectionError('not connected to the Anker cloud')
        try:
            await connection.publish(topic, payload)
        except ChargerConnectionError as error:
            await self._drop(connection, error)
            raise

    async def close(self) -> None:
        """Close every channel and the connection."""

        for channel in list(self._channels):
            await channel.close()
        async with self._lock:
            connection = self._connection
            self._connection = None
            self._stop_reader()
            if connection is not None:
                await connection.close()

    async def _connect(self) -> tuple[MqttConnection, MqttCredentials]:
        credentials = await self._credentials_provider()
        tls = await self._tls_factory(credentials)
        client_id = credentials.client_id(random.randrange(100_000))
        endpoint = MqttEndpoint(credentials.endpoint, self._port, client_id,
                                tls, self._keepalive)
        connection = await self._connector.connect(endpoint)
        logger.debug('connected to the Anker MQTT broker')
        self._connection = connection
        self._credentials = credentials
        self._reader = asyncio.create_task(self._read(connection),
                                           name='ankerprime-mqtt-reader')
        return connection, credentials

    async def _read(self, connection: MqttConnection) -> None:
        error: Exception = ChargerConnectionError('MQTT connection ended')
        try:
            async for message in connection.messages():
                self._route(message)
        except ChargerConnectionError as lost:
            error = lost
        if self._connection is connection:
            await self._drop(connection, error)

    def _route(self, message: MqttMessage) -> None:
        for channel in list(self._channels):
            topic = channel.subscription_topic
            if topic is None or not topic_belongs_to(message.topic, topic):
                continue
            try:
                channel.receive(message.payload)
            except Exception:
                logger.exception('channel listener failed')

    async def _unsubscribe(self, connection: MqttConnection,
                           topic: str) -> None:
        try:
            await connection.unsubscribe(topic)
        except ChargerConnectionError as error:
            await self._drop(connection, error)

    async def _drop(self, connection: MqttConnection,
                    error: Exception) -> None:
        """Forget a lost connection once and tell every channel."""

        if self._connection is not connection:
            return
        logger.debug('Anker MQTT connection lost: %s', error)
        self._connection = None
        self._stop_reader()
        channels = list(self._channels)
        self._channels.clear()
        for channel in channels:
            channel.lose(error)
        await connection.close()

    def _stop_reader(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()


def _file_tls_factory(directory: Path | None) -> TlsFactory:
    async def factory(credentials: MqttCredentials) -> ssl.SSLContext | None:
        return await asyncio.to_thread(load_tls_context, credentials,
                                       directory)

    return factory


def _utc_now() -> datetime:
    return datetime.now(UTC)
