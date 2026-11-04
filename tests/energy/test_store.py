"""Energy record document tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ankerprime.energy.store import EnergyRecord, EnergyStore, new_reset_marker
from ankerprime.model import PortId
from ankerprime.storage.json_store import JsonFileStore
from tests.utils import MemoryStore


SAVED_AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


async def test_round_trip_through_json_files(tmp_path: Path) -> None:
    store = EnergyStore.for_serial(JsonFileStore(tmp_path), 'AZVD4F1234')
    assert store.key == 'energy-azvd4f1234'
    assert await store.load() is None
    record = EnergyRecord({PortId.C1: 1.25, PortId.A2: 0.0},
                          {PortId.C1: 'marker'})
    await store.save(record, SAVED_AT)
    assert (tmp_path / 'energy-azvd4f1234.json').exists()
    assert await store.load() == record


async def test_document_format() -> None:
    documents = MemoryStore()
    record = EnergyRecord({PortId.C2: 3.5}, {PortId.C2: 'marker'})
    await EnergyStore(documents, 'key').save(record, SAVED_AT)
    assert documents.documents['key'] == {
        'version': 1, 'counters': {'c2': 3.5}, 'resets': {'c2': 'marker'},
        'saved_at': '2026-11-04T12:00:00+00:00'}


async def test_unknown_ports_are_skipped_and_resets_optional() -> None:
    documents = MemoryStore({'key': {'version': 1,
                                     'counters': {'c1': 2, 'c9': 1.0}}})
    record = await EnergyStore(documents, 'key').load()
    assert record == EnergyRecord({PortId.C1: 2.0})


@pytest.mark.parametrize('document', [
    {'version': 2, 'counters': {}},
    {'version': 1},
    {'version': 1, 'counters': []},
    {'version': 1, 'counters': {'c1': 'many'}},
    {'version': 1, 'counters': {'c1': True}},
    {'version': 1, 'counters': {'c1': -1.0}},
    {'version': 1, 'counters': {'c1': float('nan')}},
    {'version': 1, 'counters': {}, 'resets': []},
    {'version': 1, 'counters': {}, 'resets': {'c1': 5}},
])
async def test_unusable_documents_load_as_none(document: dict[str, Any]
                                               ) -> None:
    store = EnergyStore(MemoryStore({'key': document}), 'key')
    assert await store.load() is None


async def test_reset_zeroes_ports_with_new_markers() -> None:
    documents = MemoryStore()
    store = EnergyStore(documents, 'key')
    await store.save(EnergyRecord({PortId.C1: 4.0, PortId.C2: 2.0},
                                  {PortId.C1: 'old'}), SAVED_AT)
    record = await store.reset([PortId.C1, PortId.C3], SAVED_AT)
    assert dict(record.counters) == {PortId.C1: 0.0, PortId.C2: 2.0,
                                     PortId.C3: 0.0}
    assert record.resets[PortId.C1] != 'old'
    assert record.resets[PortId.C1].startswith('2026-11-04T12:00:00+00:00#')
    assert record.resets[PortId.C1] != record.resets[PortId.C3]
    assert await store.load() == record


def test_markers_are_unique() -> None:
    assert new_reset_marker(SAVED_AT) != new_reset_marker(SAVED_AT)
