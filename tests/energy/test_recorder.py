"""Energy recorder tests: persistence and merging with other processes."""

from datetime import UTC, datetime

import pytest

from ankerprime.energy.recorder import EnergyRecorder
from ankerprime.energy.store import EnergyRecord, EnergyStore
from ankerprime.model import PortId, ProtocolError
from tests.utils import MemoryStore


PORTS = (PortId.C1, PortId.C2)
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
WATT_HOUR = 3600.0  # watts drawn for one second = 1 Wh


def recorder(documents: MemoryStore) -> EnergyRecorder:
    return EnergyRecorder(PORTS, EnergyStore(documents, 'key'),
                          label='test', save_interval=10.0)


def count(target: EnergyRecorder, start: float, watt_hours: int,
          port: PortId = PortId.C1) -> float:
    """Feed samples adding the given Wh; return the time after them."""

    for step in range(watt_hours + 1):
        target.record(start + step, {port: WATT_HOUR})
    target.interrupt()
    return start + watt_hours + 1


async def stored(documents: MemoryStore) -> EnergyRecord:
    record = await EnergyStore(documents, 'key').load()
    assert record is not None
    return record


async def test_without_store_nothing_is_saved() -> None:
    target = EnergyRecorder(PORTS)
    count(target, 0.0, 2)
    assert not target.save_due(100.0)
    assert not await target.load()
    assert not await target.save(100.0, NOW)
    assert target.counters[PortId.C1] == pytest.approx(2.0)


async def test_store_attached_later_continues_from_it() -> None:
    documents = MemoryStore()
    await EnergyStore(documents, 'key').save(
        EnergyRecord({PortId.C1: 5.0}), NOW)
    target = EnergyRecorder(PORTS)
    count(target, 0.0, 2)
    assert not target.has_store
    target.attach(EnergyStore(documents, 'key'))
    target.attach(EnergyStore(documents, 'other'))
    assert target.has_store
    assert await target.load()
    assert target.counters[PortId.C1] == pytest.approx(5.0)
    count(target, 10.0, 1)
    await target.save(20.0, NOW)
    assert (await stored(documents)).counters[PortId.C1] == pytest.approx(6.0)


async def test_load_save_and_save_interval() -> None:
    documents = MemoryStore()
    await EnergyStore(documents, 'key').save(
        EnergyRecord({PortId.C1: 5.0}), NOW)
    target = recorder(documents)
    assert await target.load()
    assert not await target.load()
    assert target.counters[PortId.C1] == 5.0
    assert not target.save_due(0.0)
    count(target, 0.0, 2)
    assert target.save_due(0.0)
    assert not await target.save(3.0, NOW)
    assert (await stored(documents)).counters[PortId.C1] == pytest.approx(7.0)
    count(target, 3.0, 1)
    assert not target.save_due(12.0)
    assert target.save_due(13.0)


async def test_own_counters_win_without_a_reset() -> None:
    documents = MemoryStore()
    first, second = recorder(documents), recorder(documents)
    count(first, 0.0, 3)
    count(second, 0.0, 3)
    await first.save(5.0, NOW)
    await second.save(5.0, NOW)
    assert (await stored(documents)).counters[PortId.C1] == pytest.approx(3.0)


async def test_reset_elsewhere_restarts_the_port() -> None:
    documents = MemoryStore()
    target = recorder(documents)
    count(target, 0.0, 4)
    count(target, 10.0, 1, PortId.C2)
    await target.save(20.0, NOW)
    await EnergyStore(documents, 'key').reset([PortId.C1], NOW)
    count(target, 30.0, 2)
    assert await target.save(40.0, NOW)
    assert target.counters[PortId.C1] == pytest.approx(2.0)
    assert target.counters[PortId.C2] == pytest.approx(1.0)
    record = await stored(documents)
    assert record.counters[PortId.C1] == pytest.approx(2.0)
    count(target, 50.0, 1)
    assert not await target.save(60.0, NOW)
    assert target.counters[PortId.C1] == pytest.approx(3.0)


async def test_own_reset_is_stored_with_a_marker() -> None:
    documents = MemoryStore()
    target, other = recorder(documents), recorder(documents)
    count(target, 0.0, 3)
    count(other, 0.0, 3)
    await other.save(5.0, NOW)
    target.reset(None, NOW)
    assert dict(target.counters) == {PortId.C1: 0.0, PortId.C2: 0.0}
    assert target.save_due(5.0)
    await target.save(5.0, NOW)
    record = await stored(documents)
    assert set(record.resets) == {PortId.C1, PortId.C2}
    assert record.counters[PortId.C1] == 0.0
    assert await other.save(20.0, NOW)
    assert dict(other.counters) == {PortId.C1: 0.0, PortId.C2: 0.0}
    count(other, 20.0, 1)
    assert not await other.save(30.0, NOW)
    assert other.counters[PortId.C1] == pytest.approx(1.0)


async def test_idle_sync_does_not_write() -> None:
    documents = MemoryStore()
    target = recorder(documents)
    count(target, 0.0, 1)
    await target.save(5.0, NOW)
    assert documents.saves == 1
    assert target.save_due(15.0)
    assert not await target.save(15.0, NOW)
    assert documents.saves == 1


async def test_reset_of_selected_ports_ignores_unknown() -> None:
    target = recorder(MemoryStore())
    count(target, 0.0, 2)
    count(target, 10.0, 2, PortId.C2)
    target.reset([PortId.C2, PortId.A1], NOW)
    assert dict(target.counters) == {PortId.C1: pytest.approx(2.0),
                                     PortId.C2: 0.0}


async def test_failures_are_logged_and_retried(
        caplog: pytest.LogCaptureFixture) -> None:
    documents = MemoryStore()
    target = recorder(documents)
    documents.fail(ProtocolError('broken'))
    assert not await target.load()
    count(target, 0.0, 2)
    await target.save(5.0, NOW)
    assert 'not loaded' in caplog.text and 'not saved' in caplog.text
    documents.fail(None)
    assert not target.save_due(10.0)
    assert target.save_due(15.0)
    await target.save(15.0, NOW)
    assert (await stored(documents)).counters[PortId.C1] == pytest.approx(2.0)


async def test_failed_save_keeps_a_pending_reset() -> None:
    documents = MemoryStore()
    target = recorder(documents)
    count(target, 0.0, 2)
    await target.save(5.0, NOW)

    class FailingSave(MemoryStore):
        async def save(self, key: str, document: dict[str, object]) -> None:
            raise OSError('disk full')

    failing = FailingSave(documents.documents)
    target = recorder(failing)
    await target.load()
    target.reset(None, NOW)
    await target.save(10.0, NOW)
    assert target.save_due(20.0)
