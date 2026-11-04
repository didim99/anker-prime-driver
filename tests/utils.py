"""Helpers shared by several test modules."""

import asyncio
import ssl
from collections.abc import Callable
from datetime import UTC, datetime

from ankerprime.cloud.auth import Authentication
from ankerprime.cloud.client import MqttCredentials
from ankerprime.cloud.regions import EU_SERVER


DEFAULT_EXPIRY = 2_000_000_000


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
