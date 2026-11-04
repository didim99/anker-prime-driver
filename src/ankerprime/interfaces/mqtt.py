"""Minimal MQTT client surface used by the cloud transport.

Only what the A2345 cloud path needs: QoS 0 subscriptions and publishes over
one connection. The aiomqtt adapter implements it; tests use a fake.
"""

import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(init=False)
class MqttMessage:
    """One inbound PUBLISH."""

    _topic: str
    _payload: bytes

    def __init__(self, topic: str, payload: bytes):
        self._topic = topic
        self._payload = payload

    @property
    def topic(self) -> str:
        return self._topic

    @property
    def payload(self) -> bytes:
        return self._payload


@dataclass(init=False)
class MqttEndpoint:
    """Where and as whom to connect."""

    _host: str
    _port: int
    _client_id: str
    _tls: ssl.SSLContext | None
    _keepalive: int

    def __init__(self, host: str, port: int, client_id: str,
                 tls: ssl.SSLContext | None, keepalive: int):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._tls = tls
        self._keepalive = keepalive

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def tls(self) -> ssl.SSLContext | None:
        return self._tls

    @property
    def keepalive(self) -> int:
        """Seconds between keep-alive pings."""

        return self._keepalive


class MqttConnection(ABC):
    """An established MQTT 3.1.1 connection with a clean session.

    Every method raises ``ChargerConnectionError`` once the connection is lost.
    """

    @abstractmethod
    async def subscribe(self, topic: str) -> None:
        pass

    @abstractmethod
    async def unsubscribe(self, topic: str) -> None:
        pass

    @abstractmethod
    async def publish(self, topic: str, payload: bytes) -> None:
        pass

    @abstractmethod
    def messages(self) -> AsyncIterator[MqttMessage]:
        """Inbound messages; ends or raises when the connection is lost."""

        pass

    @abstractmethod
    async def close(self) -> None:
        """Disconnect; does nothing when already disconnected."""

        pass


class MqttConnector(ABC):
    """Opens MQTT connections."""

    @abstractmethod
    async def connect(self, endpoint: MqttEndpoint) -> MqttConnection:
        """Connect; raises ``ChargerConnectionError`` on failure."""

        pass
