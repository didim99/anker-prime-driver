"""Anker cloud account: device discovery and cloud-connected chargers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from ankerprime.cloud.auth import (
    AccountClient, AccountCredentials, Authentication)
from ankerprime.cloud.client import BoundDevice, CloudClient, MqttCredentials
from ankerprime.cloud.mini_power import MiniPowerClient
from ankerprime.device.a2345 import A2345Device, A2345Timings
from ankerprime.energy.store import EnergyStore
from ankerprime.interfaces.charger import Charger
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.mqtt import MqttConnector
from ankerprime.interfaces.storage import DocumentStore
from ankerprime.model.enums import ProductCode
from ankerprime.model.errors import AuthenticationError, UnknownProductError
from ankerprime.model.state import DeviceInfo
from ankerprime.transport.aiomqtt_client import AiomqttConnector
from ankerprime.transport.cloud import CloudMqttSession, TlsFactory
from ankerprime.utils import Clock, copy_with, utc_now


logger = logging.getLogger(__name__)

ResultT = TypeVar('ResultT')

AuthenticationListener = Callable[[Authentication], None]

CLOUD_PRODUCTS = frozenset({ProductCode.A2345})


class AccountOptions:
    """How a ``CloudAccount`` connects, persists and keeps time.

    Every option is optional: ``connector`` (MQTT; default aiomqtt),
    ``tls_factory``/``tls_directory`` (MQTT TLS context or where the client
    certificate files go), ``on_authentication`` (called with every new
    token, to persist it), ``energy_store`` (energy counters per charger),
    ``timings`` (of every charger) and ``clock``.
    """

    _connector: MqttConnector | None
    _tls_factory: TlsFactory | None
    _tls_directory: Path | None
    _on_authentication: AuthenticationListener | None
    _energy_store: DocumentStore | None
    _timings: A2345Timings | None
    _clock: Clock

    def __init__(self, *, connector: MqttConnector | None = None,
                 tls_factory: TlsFactory | None = None,
                 tls_directory: Path | None = None,
                 on_authentication: AuthenticationListener | None = None,
                 energy_store: DocumentStore | None = None,
                 timings: A2345Timings | None = None,
                 clock: Clock = utc_now):
        self._connector = connector
        self._tls_factory = tls_factory
        self._tls_directory = tls_directory
        self._on_authentication = on_authentication
        self._energy_store = energy_store
        self._timings = timings
        self._clock = clock

    @property
    def connector(self) -> MqttConnector | None:
        return self._connector

    @property
    def tls_factory(self) -> TlsFactory | None:
        return self._tls_factory

    @property
    def tls_directory(self) -> Path | None:
        return self._tls_directory

    @property
    def on_authentication(self) -> AuthenticationListener | None:
        return self._on_authentication

    @property
    def energy_store(self) -> DocumentStore | None:
        return self._energy_store

    @property
    def timings(self) -> A2345Timings | None:
        return self._timings

    @property
    def clock(self) -> Clock:
        return self._clock

    def replace(self, **changes: Any) -> AccountOptions:
        """A copy with the given options changed."""

        return copy_with(self, changes)


class CloudAccount:
    """A signed-in Anker account and the chargers reached through it.

    With stored ``AccountCredentials`` an expired or revoked token is
    replaced by signing in again once; without them ``AuthenticationError``
    reaches the caller. Every new token is passed to the options' listener
    so the embedder can persist it. With an ``energy_store`` the energy
    counters of each charger persist across restarts.
    """

    _http: HttpClient
    _credentials: AccountCredentials | None
    _options: AccountOptions

    _authentication: Authentication
    _client: CloudClient
    _login_lock: asyncio.Lock
    _session: CloudMqttSession
    _chargers: dict[str, A2345Device]
    _requester: _AccountRequester

    def __init__(self, http: HttpClient, authentication: Authentication, *,
                 credentials: AccountCredentials | None = None,
                 options: AccountOptions | None = None):
        options = options or AccountOptions()
        self._http = http
        self._credentials = credentials
        self._options = options
        self._authentication = authentication
        self._client = CloudClient(http, authentication, options.clock)
        self._login_lock = asyncio.Lock()
        self._session = CloudMqttSession(
            self._mqtt_credentials, options.connector or AiomqttConnector(),
            tls_factory=options.tls_factory,
            tls_directory=options.tls_directory, clock=options.clock)
        self._chargers = {}
        self._requester = _AccountRequester(self)

    @property
    def authentication(self) -> Authentication:
        return self._authentication

    @property
    def user_id(self) -> str:
        return self._authentication.user_id

    @property
    def options(self) -> AccountOptions:
        return self._options

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
        options = self._options
        energy = None
        if options.energy_store is not None:
            energy = EnergyStore.for_serial(options.energy_store,
                                            device.serial)
        charger = A2345Device(self._session.channel(device), info,
                              account=MiniPowerClient(self._requester,
                                                      device.serial),
                              energy_store=energy, timings=options.timings,
                              clock=options.clock)
        self._chargers[device.serial] = charger
        return charger

    async def close(self) -> None:
        """Disconnect every charger and the MQTT connection."""

        for charger in self._chargers.values():
            await charger.disconnect()
        await self._session.close()

    async def _mqtt_credentials(self) -> MqttCredentials:
        return await self._call(lambda client: client.mqtt_credentials())

    async def call(self, request: Callable[[CloudClient], Awaitable[ResultT]]
                   ) -> ResultT:
        """Run a request with the current token, signing in again once."""

        return await self._call(request)

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
            client = AccountClient(self._http, self._options.clock)
            authentication = await client.login(credentials.email,
                                                credentials.password,
                                                credentials.country)
            self._set_authentication(authentication)

    def _set_authentication(self, authentication: Authentication) -> None:
        self._authentication = authentication
        self._client = CloudClient(self._http, authentication,
                                   self._options.clock)
        listener = self._options.on_authentication
        if listener is not None:
            listener(authentication)

    @classmethod
    async def login(cls, http: HttpClient, credentials: AccountCredentials, *,
                    keep_credentials: bool = True,
                    options: AccountOptions | None = None) -> CloudAccount:
        """Sign in and return the account.

        ``keep_credentials`` enables automatic re-login with the same
        credentials.
        """

        options = options or AccountOptions()
        client = AccountClient(http, options.clock)
        authentication = await client.login(credentials.email,
                                            credentials.password,
                                            credentials.country)
        kept = credentials if keep_credentials else None
        return cls(http, authentication, credentials=kept, options=options)

    @classmethod
    async def login_phone(cls, http: HttpClient, phone: str, code: str, *,
                          options: AccountOptions | None = None
                          ) -> CloudAccount:
        """Sign in a mainland China account with an SMS code.

        Request the code first with ``AccountClient.request_phone_code``.
        Without a password there is no automatic re-login: an expired or
        revoked token raises ``AuthenticationError`` and needs a new code.
        """

        options = options or AccountOptions()
        client = AccountClient(http, options.clock)
        authentication = await client.login_phone(phone, code)
        return cls(http, authentication, options=options)


class _AccountRequester:
    """``CloudRequester`` running every request with the account's re-login."""

    _account: CloudAccount

    def __init__(self, account: CloudAccount):
        self._account = account

    @property
    def country(self) -> str:
        return self._account.authentication.country

    @property
    def user_id(self) -> str:
        return self._account.authentication.user_id

    async def request(self, path: str, body: Mapping[str, Any] | None = None,
                      headers: Mapping[str, str] | None = None) -> Any:
        return await self._account.call(
            lambda client: client.request(path, body, headers))

    async def upload(self, url: str, body: bytes, content_type: str) -> None:
        await self._account.call(
            lambda client: client.upload(url, body, content_type))
