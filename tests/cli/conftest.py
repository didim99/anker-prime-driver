"""Fixtures shared by the CLI tests."""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.cli.harness import Cli
from tests.cloud.fakes import FakeAnkerCloud
from tests.device.charger import charger_responder
from tests.transport.fakes import FakeMqttConnector, charger_over_mqtt


@pytest.fixture
def password(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr('sys.stdin', io.StringIO('secret\n'))
    yield


@pytest.fixture
def cli(tmp_path: Path, password: None) -> Cli:
    connector = FakeMqttConnector(charger_over_mqtt(charger_responder()))
    return Cli(tmp_path / 'config', FakeAnkerCloud(), connector)
