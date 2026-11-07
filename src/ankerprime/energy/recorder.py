"""Energy meter with persistence, shared by the device implementations.

Several processes may count the same charger into one store (two CLI
``monitor`` runs, or a ``monitor`` and an ``energy-reset``). Saving therefore
merges with the stored record: for most ports the saving process's own
counters win, so the same energy is never counted twice. A port whose stored
reset marker differs from the one this recorder last saw was reset elsewhere;
it continues from the stored value plus what this recorder counted since it
last synchronised with the store. Synchronising runs every save interval,
also when nothing was counted, so an idle recorder follows a reset too; it
only writes when something changed.
"""

import asyncio
import logging
from collections.abc import Collection, Iterable, Mapping
from datetime import datetime

from ankerprime.energy.meter import EnergyMeter
from ankerprime.energy.store import EnergyRecord, EnergyStore, new_reset_marker
from ankerprime.model.enums import PortId
from ankerprime.model.errors import AnkerPrimeError


logger = logging.getLogger(__name__)

DEFAULT_SAVE_INTERVAL = 60.0


class EnergyRecorder:
    """Counts energy and keeps an optional ``EnergyStore`` up to date.

    Times passed in as floats are seconds on a monotonic clock; ``label``
    names the charger in log messages.
    """

    _meter: EnergyMeter
    _store: EnergyStore | None
    _label: str
    _save_interval: float

    _loaded: bool
    _dirty: bool
    _saved_at: float | None
    _synced: dict[PortId, float]
    _known_resets: dict[PortId, str]
    _pending_resets: dict[PortId, str]
    _lock: asyncio.Lock

    def __init__(self, ports: Iterable[PortId],
                 store: EnergyStore | None = None, *, label: str = '',
                 save_interval: float = DEFAULT_SAVE_INTERVAL):
        self._meter = EnergyMeter(ports)
        self._store = store
        self._label = label
        self._save_interval = save_interval
        self._loaded = False
        self._dirty = False
        self._saved_at = None
        self._synced = dict(self._meter.counters)
        self._known_resets = {}
        self._pending_resets = {}
        self._lock = asyncio.Lock()

    @property
    def counters(self) -> Mapping[PortId, float]:
        return self._meter.counters

    @property
    def has_store(self) -> bool:
        return self._store is not None

    def attach(self, store: EnergyStore) -> None:
        """Use a store found later, e.g. once the charger's serial is known.

        Counted energy is kept; ``load`` then continues from the store.
        """

        if self._store is None:
            self._store = store

    def record(self, at: float, powers: Mapping[PortId, float]) -> bool:
        """Add a power sample; return whether the counters grew."""

        grew = self._meter.record(at, powers)
        if grew:
            self._dirty = True
        return grew

    def interrupt(self) -> None:
        self._meter.interrupt()

    def reset(self, ports: Collection[PortId] | None,
              at: datetime) -> None:
        """Zero the ports (all when None); the next ``save`` stores it."""

        known = self._meter.counters
        selected = list(known) if ports is None else [
            port for port in ports if port in known]
        self._meter.replace(dict.fromkeys(selected, 0.0))
        for port in selected:
            self._pending_resets[port] = new_reset_marker(at)
            self._synced[port] = 0.0

    def save_due(self, now: float) -> bool:
        """Whether the periodic synchronisation with the store is due."""

        if self._store is None:
            return False
        if self._saved_at is None:
            return self._dirty or bool(self._pending_resets)
        return now - self._saved_at >= self._save_interval

    async def load(self) -> bool:
        """Continue from the stored counters, once; return whether loaded."""

        if self._store is None or self._loaded:
            return False
        self._loaded = True
        try:
            record = await self._store.load()
        except (AnkerPrimeError, OSError) as error:
            logger.warning('energy counters of %s not loaded: %s',
                           self._label, error)
            return False
        if record is None:
            return False
        self._meter.restore(record.counters)
        self._synced = dict(self._meter.counters)
        self._known_resets = dict(record.resets)
        return True

    async def save(self, now: float, saved_at: datetime) -> bool:
        """Synchronise with the store; return whether counters changed.

        Counters change when another process reset a port. The record is
        written only when something was counted, reset or merged.
        """

        if self._store is None:
            return False
        async with self._lock:
            self._saved_at = now
            try:
                stored = await self._store.load() or EnergyRecord()
            except (AnkerPrimeError, OSError) as error:
                logger.warning('energy counters of %s not saved: %s',
                               self._label, error)
                return False
            changed, record = self._merge(stored)
            if not (changed or self._dirty or self._pending_resets):
                return False
            sent = dict(self._pending_resets)
            self._dirty = False
            try:
                await self._store.save(record, saved_at)
            except (AnkerPrimeError, OSError) as error:
                self._dirty = True
                logger.warning('energy counters of %s not saved: %s',
                               self._label, error)
                return changed
            except asyncio.CancelledError:
                self._dirty = True
                raise
            for port, marker in sent.items():
                if self._pending_resets.get(port) == marker:
                    del self._pending_resets[port]
            return changed

    def _merge(self, stored: EnergyRecord) -> tuple[bool, EnergyRecord]:
        """Combine own counters with the stored record (no awaiting here)."""

        counters = dict(self._meter.counters)
        resets = dict(stored.resets)
        changed = False
        for port, value in counters.items():
            pending = self._pending_resets.get(port)
            if pending is not None:
                resets[port] = pending
            elif stored.resets.get(port) != self._known_resets.get(port):
                counted = max(value - self._synced.get(port, 0.0), 0.0)
                counters[port] = stored.counters.get(port, 0.0) + counted
                changed = True
        self._meter.replace(counters)
        self._synced = counters
        self._known_resets = resets
        return changed, EnergyRecord(counters, resets)
