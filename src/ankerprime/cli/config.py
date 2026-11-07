"""Owner-only JSON files in a config folder: login, chargers, energy.

The folder is ``$ANKERPRIME_CONFIG_DIR`` when set, otherwise
``%APPDATA%\\ankerprime`` on Windows and ``$XDG_CONFIG_HOME/ankerprime``
(default ``~/.config/ankerprime``) elsewhere.
"""

import os
import sys
from pathlib import Path

from ankerprime.cli.aliases import ChargerAliases
from ankerprime.cloud.auth import AccountCredentials, Authentication
from ankerprime.interfaces.storage import DocumentStore, JsonDocument
from ankerprime.protocol.a2687.handshake import new_client_id
from ankerprime.storage.json_store import JsonFileStore


CONFIG_DIR_VARIABLE = 'ANKERPRIME_CONFIG_DIR'
APP_DIRECTORY = 'ankerprime'
CREDENTIALS_KEY = 'credentials'
AUTHENTICATION_KEY = 'authentication'
BLUETOOTH_KEY = 'bluetooth'
CHARGERS_KEY = 'chargers'
CLIENT_ID_FIELD = 'client_id'


class ReadOnlyStore(DocumentStore):
    """Reads another store and ignores writes.

    Short commands show the stored energy counters, but only ``monitor``
    saves them; a short command saving its older copy would undo the counts
    of a ``monitor`` running at the same time.
    """

    _store: DocumentStore

    def __init__(self, store: DocumentStore):
        self._store = store

    async def load(self, key: str) -> JsonDocument | None:
        return await self._store.load(key)

    async def save(self, key: str, document: JsonDocument) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass


class CliConfig:
    """Stored login: credentials for re-login and the current token.

    The same folder holds the energy counters of each charger.
    """

    _store: JsonFileStore

    def __init__(self, directory: Path):
        self._store = JsonFileStore(directory)

    @property
    def directory(self) -> Path:
        return self._store.directory

    def energy_store(self, writable: bool) -> DocumentStore:
        if writable:
            return self._store
        return ReadOnlyStore(self._store)

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

    async def client_id(self) -> str:
        """Bluetooth user identity of this installation, created once."""

        document = await self._store.load(BLUETOOTH_KEY) or {}
        client_id = document.get(CLIENT_ID_FIELD)
        if isinstance(client_id, str) and client_id:
            return client_id
        client_id = new_client_id()
        await self._store.save(BLUETOOTH_KEY, {**document,
                                               CLIENT_ID_FIELD: client_id})
        return client_id

    async def aliases(self) -> ChargerAliases:
        return ChargerAliases.from_document(
            await self._store.load(CHARGERS_KEY))

    async def save_aliases(self, aliases: ChargerAliases) -> None:
        await self._store.save(CHARGERS_KEY, aliases.to_document())

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
