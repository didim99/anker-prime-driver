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
from ankerprime.cloud.mini_power import MiniPowerClient
from ankerprime.device.a2345 import A2345Device, A2345Timings
from ankerprime.energy.store import EnergyStore
from ankerprime.interfaces.account import ChargerAccount
from ankerprime.interfaces.charger import Charger
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.mqtt import MqttConnector
from ankerprime.interfaces.storage import DocumentStore
from ankerprime.model.custom import (
    AccountSettings, ChargingProfile, CustomModeRules)
from ankerprime.model.enums import ProductCode
from ankerprime.model.pictures import ClockPicture
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
    embedder can persist it. With an ``energy_store`` the energy counters of
    each charger persist across restarts.
    """

    _http: HttpClient
    _credentials: AccountCredentials | None
    _clock: Clock
    _timings: A2345Timings | None
    _energy_store: DocumentStore | None

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
                 energy_store: DocumentStore | None = None,
                 timings: A2345Timings | None = None,
                 clock: Clock | None = None):
        self._http = http
        self._credentials = credentials
        self._clock = clock or utc_now
        self._timings = timings
        self._energy_store = energy_store
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
        energy = None
        if self._energy_store is not None:
            energy = EnergyStore.for_serial(self._energy_store, device.serial)
        charger = A2345Device(self._session.channel(device), info,
                              account=_AccountCharger(self, device.serial),
                              energy_store=energy, timings=self._timings,
                              clock=self._clock)
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
                    energy_store: DocumentStore | None = None,
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
                   on_authentication=on_authentication,
                   energy_store=energy_store, timings=timings, clock=clock)


class _AccountCharger(ChargerAccount):
    """Account-side settings of one charger, with the account's re-login."""

    _account: CloudAccount
    _serial: str

    def __init__(self, account: CloudAccount, serial: str):
        self._account = account
        self._serial = serial

    @property
    def mainland(self) -> bool:
        country = self._account.authentication.country
        return country == MiniPowerClient.MAINLAND_COUNTRY

    async def settings(self) -> AccountSettings:
        return await self._account.call(
            lambda client: self._client(client).settings())

    async def set_custom_mode_enabled(self, enabled: bool) -> None:
        await self._account.call(
            lambda client: self._client(client).set_custom_mode_enabled(
                enabled))

    async def set_max_compatibility(self, enabled: bool) -> None:
        await self._account.call(
            lambda client: self._client(client).set_max_compatibility(
                enabled))

    async def set_device_identification(self, enabled: bool) -> None:
        await self._account.call(
            lambda client: self._client(client).set_device_identification(
                enabled))

    async def profiles(self) -> list[ChargingProfile]:
        return await self._account.call(
            lambda client: self._client(client).profiles())

    async def add_profile(self, profile: ChargingProfile,
                          rules: CustomModeRules) -> None:
        await self._account.call(
            lambda client: self._client(client).add_profile(profile, rules))

    async def update_profile(self, profile: ChargingProfile,
                             rules: CustomModeRules) -> None:
        await self._account.call(
            lambda client: self._client(client).update_profile(profile,
                                                               rules))

    async def delete_profile(self, profile_id: int) -> None:
        await self._account.call(
            lambda client: self._client(client).delete_profile(profile_id))

    async def clock_pictures(self, language: str = 'en'
                             ) -> list[ClockPicture]:
        return await self._account.call(
            lambda client: self._client(client).clock_pictures(language))

    async def own_pictures(self) -> list[ClockPicture]:
        return await self._account.call(
            lambda client: self._client(client).own_pictures())

    async def upload_own_picture(self, jpeg: bytes) -> None:
        await self._account.call(
            lambda client: self._client(client).upload_own_picture(jpeg))

    async def delete_own_picture(self, picture_id: int) -> None:
        await self._account.call(
            lambda client: self._client(client).delete_own_picture(
                picture_id))

    def _client(self, client: CloudClient) -> MiniPowerClient:
        return MiniPowerClient(client, self._serial)
