"""Energy counters of one charger as a ``DocumentStore`` document."""

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Self

from ankerprime.interfaces.storage import DocumentStore, JsonDocument
from ankerprime.model.enums import PortId


DOCUMENT_VERSION = 1
KEY_PREFIX = 'energy-'


class EnergyStore:
    """Loads and saves the watt-hour counters of one charger.

    The document is ``{"version": 1, "counters": {"c1": 12.5, ...},
    "saved_at": "<ISO time>"}``; ``saved_at`` is informational.
    """

    _store: DocumentStore
    _key: str

    def __init__(self, store: DocumentStore, key: str):
        self._store = store
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    async def load(self) -> dict[PortId, float] | None:
        """Stored counters; None when absent or unusable.

        Unknown ports are skipped, so a document survives model changes.
        """

        document = await self._store.load(self._key)
        if document is None:
            return None
        return counters_from_document(document)

    async def save(self, counters: Mapping[PortId, float],
                   saved_at: datetime) -> None:
        await self._store.save(self._key,
                               counters_to_document(counters, saved_at))

    @classmethod
    def for_serial(cls, store: DocumentStore, serial: str) -> Self:
        return cls(store, KEY_PREFIX + serial.lower())


def counters_to_document(counters: Mapping[PortId, float],
                         saved_at: datetime) -> JsonDocument:
    return {'version': DOCUMENT_VERSION,
            'counters': {port.value: value for port, value in counters.items()},
            'saved_at': saved_at.isoformat()}


def counters_from_document(document: JsonDocument
                           ) -> dict[PortId, float] | None:
    counters = document.get('counters')
    if document.get('version') != DOCUMENT_VERSION or not isinstance(
            counters, dict):
        return None
    known = {port.value: port for port in PortId}
    result = {}
    for name, value in counters.items():
        port = known.get(name)
        if port is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        result[port] = float(value)
    return result
