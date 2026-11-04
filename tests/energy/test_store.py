"""Energy counter document tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ankerprime.energy.store import EnergyStore
from ankerprime.model import PortId
from ankerprime.storage.json_store import JsonFileStore
from tests.utils import MemoryStore


SAVED_AT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


async def test_round_trip_through_json_files(tmp_path: Path) -> None:
    store = EnergyStore.for_serial(JsonFileStore(tmp_path), 'AZVD4F1234')
    assert store.key == 'energy-azvd4f1234'
    assert await store.load() is None
    await store.save({PortId.C1: 1.25, PortId.A2: 0.0}, SAVED_AT)
    assert (tmp_path / 'energy-azvd4f1234.json').exists()
    assert await store.load() == {PortId.C1: 1.25, PortId.A2: 0.0}


async def test_document_format() -> None:
    documents = MemoryStore()
    await EnergyStore(documents, 'key').save({PortId.C2: 3.5}, SAVED_AT)
    assert documents.documents['key'] == {
        'version': 1, 'counters': {'c2': 3.5},
        'saved_at': '2026-11-04T12:00:00+00:00'}


async def test_unknown_ports_are_skipped() -> None:
    documents = MemoryStore({'key': {'version': 1,
                                     'counters': {'c1': 2, 'c9': 1.0}}})
    assert await EnergyStore(documents, 'key').load() == {PortId.C1: 2.0}


@pytest.mark.parametrize('document', [
    {'version': 2, 'counters': {}},
    {'version': 1},
    {'version': 1, 'counters': []},
    {'version': 1, 'counters': {'c1': 'many'}},
    {'version': 1, 'counters': {'c1': True}},
    {'version': 1, 'counters': {'c1': -1.0}},
    {'version': 1, 'counters': {'c1': float('nan')}},
])
async def test_unusable_documents_load_as_none(document: dict[str, Any]
                                               ) -> None:
    store = EnergyStore(MemoryStore({'key': document}), 'key')
    assert await store.load() is None
