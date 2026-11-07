"""The CLI run against one fake cloud and broker."""

import asyncio
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ankerprime.cli.config import CliConfig
from ankerprime.cli.main import main
from ankerprime.cloud.auth import AccountCredentials, Authentication
from ankerprime.interfaces.http import HttpClient
from tests.cloud.fakes import FakeAnkerCloud, FakeHttpClient
from tests.transport.fakes import FakeMqttConnector
from tests.utils import Terminal, no_tls


class Cli:
    """Runs the CLI against one fake cloud and broker."""

    _config_dir: Path
    _cloud: FakeAnkerCloud
    _connector: FakeMqttConnector

    def __init__(self, config_dir: Path, cloud: FakeAnkerCloud,
                 connector: FakeMqttConnector):
        self._config_dir = config_dir
        self._cloud = cloud
        self._connector = connector

    @property
    def cloud(self) -> FakeAnkerCloud:
        return self._cloud

    @property
    def connector(self) -> FakeMqttConnector:
        return self._connector

    def credentials(self) -> AccountCredentials | None:
        return asyncio.run(CliConfig(self._config_dir).credentials())

    def authentication(self) -> Authentication | None:
        return asyncio.run(CliConfig(self._config_dir).authentication())

    def run(self, *argv: str, tty: bool = False,
            color: str = 'never') -> tuple[int, str]:
        out = Terminal() if tty else io.StringIO()
        code = main(['--config-dir', str(self._config_dir), '--color', color,
                     *argv],
                    http_factory=self._http, connector=self._connector,
                    tls_factory=no_tls, out=out)
        return code, out.getvalue()

    def login(self, *extra: str) -> None:
        code, _ = self.run('login', 'a@example.com', 'jp', '--password-stdin',
                           *extra)
        assert code == 0

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[HttpClient]:
        yield FakeHttpClient(self._cloud)
