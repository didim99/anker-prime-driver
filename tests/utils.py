"""Helpers shared by several test modules."""

import asyncio
import copy
import ssl
from collections.abc import Callable
from datetime import UTC, datetime

from ankerprime.cloud.auth import Authentication
from ankerprime.cloud.client import MqttCredentials
from ankerprime.cloud.regions import EU_SERVER
from ankerprime.interfaces.storage import DocumentStore, JsonDocument


DEFAULT_EXPIRY = 2_000_000_000


class MemoryStore(DocumentStore):
    """``DocumentStore`` in a dict; optionally fails every call."""

    _documents: dict[str, JsonDocument]
    _error: Exception | None
    _saves: int

    def __init__(self, documents: dict[str, JsonDocument] | None = None):
        self._documents = dict(documents or {})
        self._error = None
        self._saves = 0

    @property
    def documents(self) -> dict[str, JsonDocument]:
        return self._documents

    @property
    def saves(self) -> int:
        return self._saves

    def fail(self, error: Exception | None) -> None:
        self._error = error

    async def load(self, key: str) -> JsonDocument | None:
        self._check()
        document = self._documents.get(key)
        return copy.deepcopy(document) if document is not None else None

    async def save(self, key: str, document: JsonDocument) -> None:
        self._check()
        self._documents[key] = copy.deepcopy(document)
        self._saves += 1

    async def delete(self, key: str) -> None:
        self._check()
        self._documents.pop(key, None)

    def _check(self) -> None:
        if self._error is not None:
            raise self._error


async def wait_until(predicate: Callable[[], bool],
                     timeout: float = 2.0) -> None:
    """Poll until the predicate holds; fail the test on timeout."""

    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def no_tls(credentials: MqttCredentials) -> ssl.SSLContext | None:
    """TLS factory for fake brokers."""

    return None


def make_mqtt_credentials(*, endpoint: str = 'aiot-mqtt-eu.anker.com',
                          certificate_pem: str = '',
                          private_key_pem: str = '') -> MqttCredentials:
    return MqttCredentials(user_id='user', app_name='anker_power',
                           thing_name='thing', certificate_id='certificate',
                           endpoint=endpoint, certificate_pem=certificate_pem,
                           private_key_pem=private_key_pem)


def make_authentication(user_id: str, *, expiry: int = DEFAULT_EXPIRY,
                        country: str = 'JP',
                        nickname: str | None = None) -> Authentication:
    expires_at = datetime.fromtimestamp(expiry, UTC)
    return Authentication(user_id=user_id, auth_token='test-token',
                          expires_at=expires_at, country=country,
                          server=EU_SERVER, nickname=nickname)
