"""A real 250W through the Anker cloud, signed in with the CLI's login.

These tests change settings of the charger and the account, then restore
them. They are collected only with ``--hardware`` (options in
``tests/conftest.py``); see the readme's development section.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from ankerprime.api.cloud import AccountOptions, CloudAccount
from ankerprime.cli.aliases import Connection
from ankerprime.cli.base import is_ready, select_device, wait_for_state
from ankerprime.cli.config import CliConfig, default_config_dir
from ankerprime.cli.main import aiohttp_client
from ankerprime.cloud.auth import Authentication
from ankerprime.interfaces.charger import Charger
from ankerprime.model.enums import ProductCode
from tests.hardware import helpers
from tests.hardware.helpers import Fingerprint, fingerprint, settled


READY_TIMEOUT = 60.0


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def charger(request: pytest.FixtureRequest
                  ) -> AsyncIterator[Charger]:
    """The connected 250W, with its full state."""

    helpers.SETTLE = request.config.getoption('hardware_settle')
    serial = request.config.getoption('hardware_serial')
    config = CliConfig(default_config_dir())
    authentication = await config.authentication()
    if authentication is None:
        pytest.exit('not signed in; run "ankerprime login" first', 2)
    credentials = await config.credentials()
    renewed: list[Authentication] = []
    options = AccountOptions(on_authentication=renewed.append)
    async with aiohttp_client() as http:
        account = CloudAccount(http, authentication, credentials=credentials,
                               options=options)
        try:
            device = select_device(await account.bound_devices(),
                                   serial or await _default_serial(config))
            if device.product_code.upper() != ProductCode.A2345:
                pytest.exit(f'{device.product_code} is not a 250W', 2)
            charger = account.charger(device)
            await charger.connect()
            await wait_for_state(charger, is_ready, READY_TIMEOUT)
            yield charger
        finally:
            await account.close()
            if renewed:
                await config.save_authentication(renewed[-1])


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def baseline(charger: Charger) -> Fingerprint:
    """Everything the tests change, as found before the first one."""

    return fingerprint(await settled(charger, account=True))


async def _default_serial(config: CliConfig) -> str | None:
    """The CLI's default charger when it is a cloud one."""

    default = (await config.aliases()).default
    if default is None or default.connection != Connection.CLOUD:
        return None
    return default.serial
