"""Named chargers of the CLI and the default one.

An alias names a cloud charger by serial or a Bluetooth charger by address,
with the owner ID and country to use for it. A Bluetooth alias learns the
charger's serial on its first connection, so ``energy-reset`` finds its
counters. Stored as the ``chargers`` document of the config folder.
"""

import argparse
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from ankerprime.interfaces.storage import JsonDocument
from ankerprime.model.errors import InvalidParameterError


DOCUMENT_VERSION = 1
NAME_PATTERN = re.compile(r'[a-z0-9][a-z0-9_.-]{0,31}')
COUNTRY_PATTERN = re.compile(r'[A-Z]{2}')


class Connection(StrEnum):
    CLOUD = 'cloud'
    BLUETOOTH = 'bluetooth'


@dataclass(init=False)
class ChargerAlias:
    """One named charger."""

    _name: str
    _connection: Connection
    _serial: str | None
    _address: str | None
    _owner_id: str | None
    _country: str | None

    def __init__(self, name: str, connection: Connection, *,
                 serial: str | None = None, address: str | None = None,
                 owner_id: str | None = None, country: str | None = None):
        self._name = name
        self._connection = connection
        self._serial = serial
        self._address = address
        self._owner_id = owner_id
        self._country = country

    @property
    def name(self) -> str:
        return self._name

    @property
    def connection(self) -> Connection:
        return self._connection

    @property
    def serial(self) -> str | None:
        """Cloud: the target; Bluetooth: learned on the first connection."""

        return self._serial

    @property
    def address(self) -> str | None:
        """Bluetooth address; None for cloud chargers."""

        return self._address

    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    @property
    def country(self) -> str | None:
        return self._country

    @property
    def target(self) -> str:
        """Serial or address, whichever selects the charger."""

        if self._connection == Connection.BLUETOOTH:
            return self._address or ''
        return self._serial or ''

    def with_serial(self, serial: str) -> Self:
        return type(self)(self._name, self._connection, serial=serial,
                          address=self._address, owner_id=self._owner_id,
                          country=self._country)

    def to_document(self) -> JsonDocument:
        document: dict[str, Any] = {'connection': self._connection.value}
        for key, value in (('serial', self._serial),
                           ('address', self._address),
                           ('owner_id', self._owner_id),
                           ('country', self._country)):
            if value is not None:
                document[key] = value
        return document

    @classmethod
    def from_document(cls, name: str, document: Any) -> Self | None:
        """Restore a stored alias; None when unusable."""

        if not isinstance(document, dict):
            return None
        try:
            connection = Connection(str(document.get('connection')))
        except ValueError:
            return None
        values = {key: document.get(key)
                  for key in ('serial', 'address', 'owner_id', 'country')}
        if not all(value is None or isinstance(value, str)
                   for value in values.values()):
            return None
        alias = cls(name, connection, **values)
        return alias if alias.target else None


@dataclass(init=False)
class ChargerAliases:
    """All aliases by name and the name of the default one."""

    _aliases: dict[str, ChargerAlias]
    _default: str | None

    def __init__(self, aliases: dict[str, ChargerAlias] | None = None,
                 default: str | None = None):
        self._aliases = dict(aliases or {})
        self._default = default if default in self._aliases else None

    @property
    def aliases(self) -> list[ChargerAlias]:
        return sorted(self._aliases.values(), key=lambda alias: alias.name)

    @property
    def default(self) -> ChargerAlias | None:
        if self._default is None:
            return None
        return self._aliases.get(self._default)

    def get(self, name: str) -> ChargerAlias:
        alias = self._aliases.get(name.lower())
        if alias is None:
            raise InvalidParameterError(
                f'no charger named {name!r}; see "ankerprime charger list"')
        return alias

    def put(self, alias: ChargerAlias) -> bool:
        """Add or replace an alias; return whether it replaced one."""

        replaced = alias.name in self._aliases
        self._aliases[alias.name] = alias
        return replaced

    def remove(self, name: str) -> ChargerAlias:
        alias = self.get(name)
        del self._aliases[alias.name]
        if self._default == alias.name:
            self._default = None
        return alias

    def set_default(self, name: str | None) -> None:
        self._default = None if name is None else self.get(name).name

    def to_document(self) -> JsonDocument:
        return {'version': DOCUMENT_VERSION, 'default': self._default,
                'chargers': {name: alias.to_document()
                             for name, alias in self._aliases.items()}}

    @classmethod
    def from_document(cls, document: JsonDocument | None) -> Self:
        """Restore stored aliases; unusable entries are skipped."""

        if document is None or document.get('version') != DOCUMENT_VERSION:
            return cls()
        entries = document.get('chargers')
        aliases = {}
        if isinstance(entries, dict):
            for name, entry in entries.items():
                alias = ChargerAlias.from_document(name, entry)
                if alias is not None and NAME_PATTERN.fullmatch(name):
                    aliases[name] = alias
        default = document.get('default')
        return cls(aliases, default if isinstance(default, str) else None)


def parse_alias_name(text: str) -> str:
    """Letters, digits, ``_ . -``; up to 32 characters; case-insensitive."""

    name = text.strip().lower()
    if not NAME_PATTERN.fullmatch(name):
        raise argparse.ArgumentTypeError(
            f'invalid charger name {text!r}: use up to 32 letters, digits, '
            f'"_", "." or "-"')
    return name


def parse_country(text: str) -> str:
    """Two-letter country code, any case."""

    code = text.strip().upper()
    if not COUNTRY_PATTERN.fullmatch(code):
        raise argparse.ArgumentTypeError(
            f'invalid country code {text!r}; expected two letters')
    return code
