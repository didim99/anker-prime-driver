"""Where the CLI keeps the account: owner-only JSON files in a config folder.

The folder is ``$ANKERPRIME_CONFIG_DIR`` when set, otherwise
``%APPDATA%\\ankerprime`` on Windows and ``$XDG_CONFIG_HOME/ankerprime``
(default ``~/.config/ankerprime``) elsewhere.
"""

import os
import sys
from pathlib import Path

from ankerprime.cloud.auth import AccountCredentials, Authentication
from ankerprime.storage.json_store import JsonFileStore


CONFIG_DIR_VARIABLE = 'ANKERPRIME_CONFIG_DIR'
APP_DIRECTORY = 'ankerprime'
CREDENTIALS_KEY = 'credentials'
AUTHENTICATION_KEY = 'authentication'


class CliConfig:
    """Stored login: credentials for re-login and the current token."""

    _store: JsonFileStore

    def __init__(self, directory: Path):
        self._store = JsonFileStore(directory)

    @property
    def directory(self) -> Path:
        return self._store.directory

    async def credentials(self) -> AccountCredentials | None:
        document = await self._store.load(CREDENTIALS_KEY)
        if document is None:
            return None
        return AccountCredentials.from_document(document)

    async def authentication(self) -> Authentication | None:
        document = await self._store.load(AUTHENTICATION_KEY)
        if document is None:
            return None
        return Authentication.from_document(document)

    async def save_login(self, authentication: Authentication,
                         credentials: AccountCredentials | None) -> None:
        """Store a new login; without credentials any stored ones are removed."""

        await self.save_authentication(authentication)
        if credentials is None:
            await self._store.delete(CREDENTIALS_KEY)
        else:
            await self._store.save(CREDENTIALS_KEY, credentials.to_document())

    async def save_authentication(self, authentication: Authentication
                                  ) -> None:
        await self._store.save(AUTHENTICATION_KEY,
                               authentication.to_document())

    async def clear(self) -> None:
        await self._store.delete(AUTHENTICATION_KEY)
        await self._store.delete(CREDENTIALS_KEY)


def default_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_VARIABLE)
    if override:
        return Path(override)
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or str(Path.home())
        return Path(base) / APP_DIRECTORY
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / APP_DIRECTORY
