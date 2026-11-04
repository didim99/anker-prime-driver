"""Energy counters of one charger as a ``DocumentStore`` document."""

import math
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Self

from ankerprime.interfaces.storage import DocumentStore, JsonDocument
from ankerprime.model.enums import PortId


DOCUMENT_VERSION = 1
KEY_PREFIX = 'energy-'


@dataclass(init=False)
class EnergyRecord:
    """Stored counters and the reset marker of each port.

    A marker changes whenever the port is reset, so a process holding its own
    counters can tell that someone else reset them.
    """

    _counters: Mapping[PortId, float]
    _resets: Mapping[PortId, str]

    def __init__(self, counters: Mapping[PortId, float] | None = None,
                 resets: Mapping[PortId, str] | None = None):
        self._counters = MappingProxyType(dict(counters or {}))
        self._resets = MappingProxyType(dict(resets or {}))

    @property
    def counters(self) -> Mapping[PortId, float]:
        """Watt-hours per port."""

        return self._counters

    @property
    def resets(self) -> Mapping[PortId, str]:
        """Marker of the last reset per port; ports never reset are absent."""

        return self._resets


class EnergyStore:
    """Loads and saves the energy record of one charger.

    The document is ``{"version": 1, "counters": {"c1": 12.5, ...},
    "resets": {"c1": "<marker>"}, "saved_at": "<ISO time>"}``; ``resets`` may
    be absent and ``saved_at`` is informational.
    """

    _store: DocumentStore
    _key: str

    def __init__(self, store: DocumentStore, key: str):
        self._store = store
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    async def load(self) -> EnergyRecord | None:
        """Stored record; None when absent or unusable.

        Unknown ports are skipped, so a document survives model changes.
        """

        document = await self._store.load(self._key)
        if document is None:
            return None
        return record_from_document(document)

    async def save(self, record: EnergyRecord, saved_at: datetime) -> None:
        await self._store.save(self._key, record_to_document(record, saved_at))

    async def reset(self, ports: Collection[PortId] | None,
                    at: datetime) -> EnergyRecord:
        """Zero the stored counters of the ports and give them new markers.

        Without ports every stored counter is reset. A process counting the
        same charger into this store notices the new markers on its next
        save and restarts those ports from zero.
        """

        record = await self.load() or EnergyRecord()
        counters = dict(record.counters)
        resets = dict(record.resets)
        for port in list(counters) if ports is None else ports:
            counters[port] = 0.0
            resets[port] = new_reset_marker(at)
        updated = EnergyRecord(counters, resets)
        await self.save(updated, at)
        return updated

    @classmethod
    def for_serial(cls, store: DocumentStore, serial: str) -> Self:
        return cls(store, KEY_PREFIX + serial.lower())


def new_reset_marker(at: datetime) -> str:
    """Unique marker: the reset time plus a random suffix."""

    return f'{at.isoformat()}#{uuid.uuid4().hex[:8]}'


def record_to_document(record: EnergyRecord,
                       saved_at: datetime) -> JsonDocument:
    return {'version': DOCUMENT_VERSION,
            'counters': {port.value: value
                         for port, value in record.counters.items()},
            'resets': {port.value: marker
                       for port, marker in record.resets.items()},
            'saved_at': saved_at.isoformat()}


def record_from_document(document: JsonDocument) -> EnergyRecord | None:
    counters = document.get('counters')
    resets = document.get('resets', {})
    if (document.get('version') != DOCUMENT_VERSION
            or not isinstance(counters, dict) or not isinstance(resets, dict)):
        return None
    known = {port.value: port for port in PortId}
    record_counters = {}
    for name, value in counters.items():
        if name not in known:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        record_counters[known[name]] = float(value)
    record_resets = {}
    for name, marker in resets.items():
        if name not in known:
            continue
        if not isinstance(marker, str):
            return None
        record_resets[known[name]] = marker
    return EnergyRecord(record_counters, record_resets)
