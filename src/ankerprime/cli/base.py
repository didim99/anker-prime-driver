"""Command base classes and the context commands run in."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, ClassVar, TextIO

from ankerprime.api.cloud import CLOUD_PRODUCTS, CloudAccount
from ankerprime.cli.config import CliConfig
from ankerprime.cli.style import PLAIN, Painter
from ankerprime.cloud.auth import Authentication
from ankerprime.cloud.client import BoundDevice
from ankerprime.interfaces.channel import Channel
from ankerprime.interfaces.charger import Charger
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.mqtt import MqttConnector
from ankerprime.model.errors import (
    AnkerPrimeError, AuthenticationError,
    ChargerTimeoutError, InvalidParameterError)
from ankerprime.model.state import ChargerState
from ankerprime.transport.cloud import TlsFactory


if TYPE_CHECKING:
    from ankerprime.transport.ble import DiscoveredCharger


HttpFactory = Callable[[], AbstractAsyncContextManager[HttpClient]]
StatePredicate = Callable[[ChargerState], bool]
# Replace the Bluetooth stack in tests: a link per address, and a scan.
BleTransportFactory = Callable[[str], Channel]
BleScanner = Callable[[float], Awaitable[list['DiscoveredCharger']]]

DEFAULT_TIMEOUT = 30.0
ERROR_POLL = 0.25
DEFAULT_COUNTRY = 'US'
BLE_EXTRA_HINT = ('Bluetooth needs the "ble" extra: '
                  'pip install "anker-prime-driver[ble]"')


class CliContext:
    """Configuration, I/O and connection factories shared by commands."""

    _config: CliConfig
    _http_factory: HttpFactory
    _connector: MqttConnector | None
    _tls_factory: TlsFactory | None
    _ble_transport: BleTransportFactory | None
    _ble_scanner: BleScanner | None
    _out: TextIO
    _painter: Painter
    _ansi: bool

    _new_authentication: Authentication | None

    def __init__(self, config: CliConfig, http_factory: HttpFactory,
                 out: TextIO, *, connector: MqttConnector | None = None,
                 tls_factory: TlsFactory | None = None,
                 ble_transport: BleTransportFactory | None = None,
                 ble_scanner: BleScanner | None = None,
                 painter: Painter = PLAIN, ansi: bool = False):
        self._config = config
        self._http_factory = http_factory
        self._connector = connector
        self._tls_factory = tls_factory
        self._ble_transport = ble_transport
        self._ble_scanner = ble_scanner
        self._out = out
        self._painter = painter
        self._ansi = ansi
        self._new_authentication = None

    @property
    def config(self) -> CliConfig:
        return self._config

    @property
    def out(self) -> TextIO:
        return self._out

    @property
    def painter(self) -> Painter:
        """Colours for ``out``; plain when colours are off."""

        return self._painter

    @property
    def ansi(self) -> bool:
        """``out`` is a terminal that interprets escape sequences."""

        return self._ansi

    def print(self, text: str = '') -> None:
        print(text, file=self._out, flush=True)

    def http(self) -> AbstractAsyncContextManager[HttpClient]:
        return self._http_factory()

    @asynccontextmanager
    async def account(self, *, save_energy: bool = False
                      ) -> AsyncIterator[CloudAccount]:
        """The stored account; a refreshed token is saved on exit.

        Energy counters are loaded from the config folder and saved there
        only with ``save_energy``.
        """

        authentication = await self._config.authentication()
        if authentication is None:
            raise AuthenticationError('not signed in; run "ankerprime login"')
        credentials = await self._config.credentials()
        energy_store = self._config.energy_store(save_energy)
        async with self._http_factory() as http:
            account = CloudAccount(http, authentication,
                                   credentials=credentials,
                                   connector=self._connector,
                                   tls_factory=self._tls_factory,
                                   on_authentication=self._remember,
                                   energy_store=energy_store)
            try:
                yield account
            finally:
                await account.close()
                await self._save_new_authentication()

    @asynccontextmanager
    async def charger(self, serial: str | None, timeout: float, *,
                      save_energy: bool = False) -> AsyncIterator[Charger]:
        """A connected charger whose full state has arrived."""

        async with self.account(save_energy=save_energy) as account:
            devices = await account.bound_devices()
            device = select_device(devices, serial)
            charger = account.charger(device)
            await charger.connect()
            await wait_for_state(charger, is_ready, timeout)
            yield charger

    @asynccontextmanager
    async def bluetooth_charger(self, address: str, *, owner_id: str | None,
                                country: str | None, timeout: float,
                                save_energy: bool = False
                                ) -> AsyncIterator[Charger]:
        """A 160W connected over Bluetooth whose full state has arrived.

        The owner ID and the country default to the signed-in account's.
        """

        try:
            from ankerprime.api.ble import ble_charger
        except ImportError:
            raise InvalidParameterError(BLE_EXTRA_HINT) from None
        if owner_id is None:
            authentication = await self._config.authentication()
            owner_id = authentication.user_id if authentication else None
        if country is None:
            credentials = await self._config.credentials()
            country = credentials.country if credentials else DEFAULT_COUNTRY
        transport = None
        if self._ble_transport is not None:
            transport = self._ble_transport(address)
        charger = ble_charger(address, owner_id=owner_id,
                              client_id=await self._config.client_id(),
                              country=country.upper(),
                              energy_store=self._config.energy_store(
                                  save_energy),
                              transport=transport)
        try:
            await charger.connect()
            await wait_for_state(charger, is_ready, timeout)
            yield charger
        finally:
            await charger.disconnect()

    async def scan(self, timeout: float) -> list[DiscoveredCharger]:
        """Chargers advertising nearby."""

        if self._ble_scanner is not None:
            return await self._ble_scanner(timeout)
        try:
            from ankerprime.api.ble import discover_chargers
        except ImportError:
            raise InvalidParameterError(BLE_EXTRA_HINT) from None
        return await discover_chargers(timeout)

    def _remember(self, authentication: Authentication) -> None:
        self._new_authentication = authentication

    async def _save_new_authentication(self) -> None:
        if self._new_authentication is not None:
            await self._config.save_authentication(self._new_authentication)
            self._new_authentication = None


class Command(ABC):
    """One sub-command."""

    NAME: ClassVar[str]
    HELP: ClassVar[str]

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Declare the command's own arguments."""

        pass

    @abstractmethod
    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        """Execute and return the exit code."""

        pass


class ChargerCommand(Command):
    """A command working on one connected charger."""

    SAVES_ENERGY: ClassVar[bool] = False

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        connection: AbstractAsyncContextManager[Charger]
        if args.ble is not None:
            connection = context.bluetooth_charger(
                args.ble, owner_id=args.owner_id, country=args.country,
                timeout=args.timeout, save_energy=self.SAVES_ENERGY)
        else:
            connection = context.charger(args.serial, args.timeout,
                                         save_energy=self.SAVES_ENERGY)
        async with connection as charger:
            return await self.run_charger(context, charger, args)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        selection = parser.add_mutually_exclusive_group()
        selection.add_argument('--serial',
                               help='cloud charger serial; needed with '
                                    'several chargers')
        selection.add_argument('--ble', metavar='ADDRESS',
                               help='Bluetooth address of a 160W charger '
                                    '(see "scan")')
        parser.add_argument('--owner-id',
                            help='Anker account ID the 160W is bound to '
                                 '(default: the signed-in account)')
        parser.add_argument('--country',
                            help='account country code for the 160W '
                                 '(default: the signed-in account\'s, '
                                 f'else {DEFAULT_COUNTRY})')
        parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                            help='seconds to wait for the charger state')
        self.add_charger_arguments(parser)

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Declare arguments beyond charger selection."""

        pass

    @abstractmethod
    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        """Execute on the connected charger and return the exit code."""

        pass


def select_device(devices: list[BoundDevice],
                  serial: str | None) -> BoundDevice:
    """Pick the charger by serial, or the only cloud charger."""

    chargers = [device for device in devices
                if device.product_code.upper() in CLOUD_PRODUCTS]
    if serial is not None:
        for device in chargers:
            if device.serial.upper() == serial.upper():
                return device
        raise InvalidParameterError(f'no cloud charger with serial {serial}')
    if len(chargers) == 1:
        return chargers[0]
    if not chargers:
        raise InvalidParameterError('the account has no cloud charger')
    serials = ', '.join(device.serial for device in chargers)
    raise InvalidParameterError(f'several chargers, pick one with --serial: '
                                f'{serials}')


def is_ready(state: ChargerState) -> bool:
    """Telemetry is live and the full state has arrived."""

    return state.available and bool(state.outputs)


async def wait_for_state(charger: Charger, predicate: StatePredicate,
                         timeout: float) -> ChargerState:
    """Wait until a state satisfies the predicate."""

    if predicate(charger.state):
        return charger.state
    reached = asyncio.Event()

    def check(state: ChargerState) -> None:
        if predicate(state):
            reached.set()

    unsubscribe = charger.subscribe(check)
    try:
        async with asyncio.timeout(timeout):
            # The charger may give up (``last_error``) without a new state.
            while charger.last_error is None and not reached.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(reached.wait(), ERROR_POLL)
    except TimeoutError:
        pass
    finally:
        unsubscribe()
    if reached.is_set():
        return charger.state
    error = charger.last_error
    if isinstance(error, AnkerPrimeError):
        raise error
    raise ChargerTimeoutError(
        f'no charger state within {timeout:.0f} s; is it online?')
