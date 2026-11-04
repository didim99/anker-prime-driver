"""Anker cloud account: device discovery and cloud-connected chargers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from ankerprime.cloud.auth import (
    AccountClient, AccountCredentials, Authentication)
from ankerprime.cloud.client import BoundDevice, CloudClient, MqttCredentials
from ankerprime.device.a2345 import A2345Device, A2345Timings
from ankerprime.interfaces.charger import Charger
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.mqtt import MqttConnector
from ankerprime.model.enums import ProductCode
from ankerprime.model.errors import AuthenticationError, UnknownProductError
from ankerprime.model.state import DeviceInfo
from ankerprime.transport.aiomqtt_client import AiomqttConnector
from ankerprime.transport.cloud import CloudMqttSession, TlsFactory
from ankerprime.utils import Clock, utc_now


logger = logging.getLogger(__name__)

ResultT = TypeVar('ResultT')

AuthenticationListener = Callable[[Authentication], None]

CLOUD_PRODUCTS = frozenset({ProductCode.A2345})


class CloudAccount:
    """A signed-in Anker account and the chargers reached through it.

    With stored ``AccountCredentials`` an expired or revoked token is
    replaced by signing in again once; without them ``AuthenticationError``
    reaches the caller. Every new token is passed to the listener so the
    embedder can persist it.
    """

    _http: HttpClient
    _credentials: AccountCredentials | None
    _clock: Clock
    _timings: A2345Timings | None

    _on_authentication: AuthenticationListener | None

    _authentication: Authentication
    _client: CloudClient
    _login_lock: asyncio.Lock
    _session: CloudMqttSession
    _chargers: dict[str, A2345Device]

    def __init__(self, http: HttpClient, authentication: Authentication, *,
                 credentials: AccountCredentials | None = None,
                 connector: MqttConnector | None = None,
                 tls_factory: TlsFactory | None = None,
                 tls_directory: Path | None = None,
                 on_authentication: AuthenticationListener | None = None,
                 timings: A2345Timings | None = None,
                 clock: Clock | None = None):
        self._http = http
        self._credentials = credentials
        self._clock = clock or utc_now
        self._timings = timings
        self._on_authentication = on_authentication
        self._authentication = authentication
        self._client = CloudClient(http, authentication, self._clock)
        self._login_lock = asyncio.Lock()
        self._session = CloudMqttSession(self._mqtt_credentials,
                                         connector or AiomqttConnector(),
                                         tls_factory=tls_factory,
                                         tls_directory=tls_directory,
                                         clock=self._clock)
        self._chargers = {}

    @property
    def authentication(self) -> Authentication:
        return self._authentication

    @property
    def user_id(self) -> str:
        return self._authentication.user_id

    async def bound_devices(self) -> list[BoundDevice]:
        """Every device bound to or shared with the account."""

        return await self._call(lambda client: client.bound_devices())

    async def chargers(self) -> list[Charger]:
        """Chargers of the account that can be reached through the cloud."""

        devices = await self.bound_devices()
        return [self.charger(device) for device in devices
                if device.product_code.upper() in CLOUD_PRODUCTS]

    def charger(self, device: BoundDevice) -> Charger:
        """Return the charger of a bound device; one instance per serial."""

        if device.product_code.upper() not in CLOUD_PRODUCTS:
            raise UnknownProductError(
                f'{device.product_code} is not reachable through the cloud')
        existing = self._chargers.get(device.serial)
        if existing is not None:
            return existing
        info = DeviceInfo(serial=device.serial, name=device.display_name,
                          firmware_version=device.software_version)
        charger = A2345Device(self._session.channel(device), info,
                              timings=self._timings, clock=self._clock)
        self._chargers[device.serial] = charger
        return charger

    async def close(self) -> None:
        """Disconnect every charger and the MQTT connection."""

        for charger in self._chargers.values():
            await charger.disconnect()
        await self._session.close()

    async def _mqtt_credentials(self) -> MqttCredentials:
        return await self._call(lambda client: client.mqtt_credentials())

    async def _call(self, request: Callable[[CloudClient],
                                            Awaitable[ResultT]]) -> ResultT:
        client = self._client
        try:
            return await request(client)
        except AuthenticationError:
            if self._credentials is None:
                raise
        await self._relogin(client)
        return await request(self._client)

    async def _relogin(self, failed: CloudClient) -> None:
        """Sign in again unless another call already replaced the token."""

        async with self._login_lock:
            if self._client is not failed or self._credentials is None:
                return
            logger.debug('Anker token rejected; signing in again')
            credentials = self._credentials
            client = AccountClient(self._http, self._clock)
            authentication = await client.login(credentials.email,
                                                credentials.password,
                                                credentials.country)
            self._set_authentication(authentication)

    def _set_authentication(self, authentication: Authentication) -> None:
        self._authentication = authentication
        self._client = CloudClient(self._http, authentication, self._clock)
        if self._on_authentication is not None:
            self._on_authentication(authentication)

    @classmethod
    async def login(cls, http: HttpClient, credentials: AccountCredentials, *,
                    keep_credentials: bool = True,
                    connector: MqttConnector | None = None,
                    tls_factory: TlsFactory | None = None,
                    tls_directory: Path | None = None,
                    on_authentication: AuthenticationListener | None = None,
                    timings: A2345Timings | None = None,
                    clock: Clock | None = None) -> CloudAccount:
        """Sign in and return the account.

        ``keep_credentials`` enables automatic re-login with the same
        credentials; the other arguments are those of the constructor.
        """

        client = AccountClient(http, clock)
        authentication = await client.login(credentials.email,
                                            credentials.password,
                                            credentials.country)
        kept = credentials if keep_credentials else None
        return cls(http, authentication, credentials=kept,
                   connector=connector, tls_factory=tls_factory,
                   tls_directory=tls_directory,
                   on_authentication=on_authentication, timings=timings,
                   clock=clock)
