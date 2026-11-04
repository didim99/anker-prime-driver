"""In-memory MQTT doubles."""

import asyncio
from collections.abc import AsyncIterator

from ankerprime.interfaces.mqtt import (
    MqttConnection, MqttConnector, MqttEndpoint, MqttMessage)
from ankerprime.model.errors import ChargerConnectionError


class FakeMqttConnection(MqttConnection):
    """Records calls; inbound messages and losses are injected by tests."""

    _subscriptions: list[str]
    _unsubscriptions: list[str]
    _published: list[tuple[str, bytes]]
    _inbox: asyncio.Queue[MqttMessage | Exception | None]
    _closed: bool
    _fail_publish: bool

    def __init__(self) -> None:
        self._subscriptions = []
        self._unsubscriptions = []
        self._published = []
        self._inbox = asyncio.Queue()
        self._closed = False
        self._fail_publish = False

    @property
    def subscriptions(self) -> list[str]:
        return self._subscriptions

    @property
    def unsubscriptions(self) -> list[str]:
        return self._unsubscriptions

    @property
    def published(self) -> list[tuple[str, bytes]]:
        return self._published

    @property
    def closed(self) -> bool:
        return self._closed

    def set_fail_publish(self, fail: bool) -> None:
        self._fail_publish = fail

    def deliver(self, topic: str, payload: bytes) -> None:
        self._inbox.put_nowait(MqttMessage(topic, payload))

    def drop(self, error: Exception) -> None:
        self._inbox.put_nowait(error)

    async def subscribe(self, topic: str) -> None:
        self._check()
        self._subscriptions.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self._check()
        self._unsubscriptions.append(topic)

    async def publish(self, topic: str, payload: bytes) -> None:
        self._check()
        if self._fail_publish:
            raise ChargerConnectionError('publish failed')
        self._published.append((topic, payload))

    async def messages(self) -> AsyncIterator[MqttMessage]:
        while True:
            item = await self._inbox.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._inbox.put_nowait(None)

    def _check(self) -> None:
        if self._closed:
            raise ChargerConnectionError('connection closed')


class FakeMqttConnector(MqttConnector):
    """Hands out fake connections; can be told to fail."""

    _connections: list[FakeMqttConnection]
    _endpoints: list[MqttEndpoint]
    _failures: list[Exception]

    def __init__(self) -> None:
        self._connections = []
        self._endpoints = []
        self._failures = []

    @property
    def connections(self) -> list[FakeMqttConnection]:
        return self._connections

    @property
    def endpoints(self) -> list[MqttEndpoint]:
        return self._endpoints

    @property
    def last(self) -> FakeMqttConnection:
        return self._connections[-1]

    def fail_next(self, error: Exception) -> None:
        self._failures.append(error)

    async def connect(self, endpoint: MqttEndpoint) -> MqttConnection:
        self._endpoints.append(endpoint)
        if self._failures:
            raise self._failures.pop(0)
        connection = FakeMqttConnection()
        self._connections.append(connection)
        return connection
