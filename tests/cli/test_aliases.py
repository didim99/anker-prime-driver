"""Named chargers and the default charger in the CLI."""

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ankerprime.cli.aliases import (
    ChargerAlias, ChargerAliases, Connection)
from ankerprime.cli.main import EXIT_ERROR
from tests.cli.test_bluetooth import ADDRESS, BluetoothCli
from tests.cli.test_commands import Cli
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


def stored(config_dir: Path) -> dict[str, object]:
    document = json.loads((config_dir / 'chargers.json').read_text())
    assert isinstance(document, dict)
    return document


def test_add_list_default_and_remove(cli: Cli, tmp_path: Path) -> None:
    code, out = cli.run('charger')
    assert code == 0 and 'No saved chargers' in out
    assert cli.run('charger', 'add', 'Desk', '--serial', 'serial123',
                   '--default')[0] == 0
    code, out = cli.run('charger', 'add', 'lamp', '--ble',
                        'aa:bb:cc:dd:ee:ff', '--owner-id', 'owner',
                        '--country', 'de')
    assert code == 0 and 'Charger lamp saved.' in out
    code, out = cli.run('charger', 'list')
    lines = out.splitlines()
    assert lines[1].split() == ['*', 'desk', 'cloud', 'SERIAL123']
    assert lines[2].split() == ['lamp', 'bluetooth', ADDRESS, 'owner',
                                'owner,', 'country', 'DE']
    assert cli.run('charger', 'default')[1].strip() == 'desk'
    assert cli.run('charger', 'default', 'lamp')[0] == 0
    assert stored(tmp_path / 'config')['default'] == 'lamp'
    assert cli.run('charger', 'remove', 'LAMP')[0] == 0
    assert cli.run('charger', 'default')[1].strip() == 'No default charger.'
    assert cli.run('charger', 'default', 'desk')[0] == 0
    assert cli.run('charger', 'default', '--clear')[0] == 0
    assert stored(tmp_path / 'config')['default'] is None


def test_replacing_keeps_one_entry(cli: Cli) -> None:
    cli.run('charger', 'add', 'desk', '--serial', 'A')
    code, out = cli.run('charger', 'add', 'desk', '--serial', 'B')
    assert 'Charger desk replaced.' in out
    assert 'B' in cli.run('charger', 'list')[1]


def test_unknown_or_invalid_names(cli: Cli,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('charger', 'remove', 'nothing')
    assert code == EXIT_ERROR
    assert 'no charger named' in capsys.readouterr().err
    for argv in (('charger', 'add', 'bad name', '--serial', 'X'),
                 ('charger', 'add', 'desk'),
                 ('charger', 'add', 'desk', '--ble', 'A', '--country', 'GER'),
                 ('status', '-c', 'desk', '--serial', 'X')):
        with pytest.raises(SystemExit):
            cli.run(*argv)


def test_cloud_commands_use_the_named_or_default_charger(
        cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    cli.login()
    cli.run('charger', 'add', 'desk', '--serial', 'SERIAL123')
    cli.run('charger', 'add', 'gone', '--serial', 'NOPE', '--default')
    code, _ = cli.run('status')
    assert code == EXIT_ERROR
    assert 'no cloud charger with serial NOPE' in capsys.readouterr().err
    code, out = cli.run('status', '-c', 'desk')
    assert code == 0 and 'Desk charger' in out
    assert cli.run('status', '--serial', 'SERIAL123')[0] == 0


def test_energy_reset_uses_the_saved_serial(cli: Cli, tmp_path: Path) -> None:
    energy_file = tmp_path / 'config' / 'energy-serial123.json'
    energy_file.parent.mkdir(parents=True)
    energy_file.write_text(json.dumps({'version': 1,
                                       'counters': {'c1': 2.5}}))
    cli.run('charger', 'add', 'desk', '--serial', 'SERIAL123', '--default')
    code, out = cli.run('energy-reset')
    assert code == 0 and 'reset: c1.' in out
    assert cli.run('energy-reset', '-c', 'desk')[0] == 0


def test_bluetooth_alias_learns_its_serial(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ble = BluetoothCli(tmp_path / 'config')
    ble.run('charger', 'add', 'lamp', '--ble', ADDRESS.lower(), '--country',
            'de', '--default')
    code, _ = ble.run('energy-reset')
    assert code == EXIT_ERROR
    assert 'not known yet' in capsys.readouterr().err
    code, out = ble.run('status')
    assert code == 0 and 'Anker Prime 160W' in out
    assert ble.addresses == [ADDRESS]
    probe = dict(ble.charger.requests)[0x020A]
    assert probe.get(0xA2) == b'\x04DE'
    alias = ChargerAliases.from_document(
        stored(tmp_path / 'config')).get('lamp')
    assert alias.serial == ble.charger.serial
    assert ble.run('energy-reset')[0] == 0
    ble.run('status', '--country', 'us')
    probe = [request for opcode, request in ble.charger.requests
             if opcode == 0x020A][-1]
    assert probe.get(0xA2) == b'\x04US'


def test_explicit_selection_beats_the_default(tmp_path: Path) -> None:
    ble = BluetoothCli(tmp_path / 'config')
    ble.run('charger', 'add', 'other', '--ble', '11:22:33:44:55:66',
            '--default')
    assert ble.run('status', '--ble', ADDRESS)[0] == 0
    assert ble.addresses == [ADDRESS]


def test_unusable_stored_entries_are_skipped() -> None:
    aliases = ChargerAliases.from_document({
        'version': 1, 'default': 'gone', 'chargers': {
            'desk': {'connection': 'cloud', 'serial': 'S1'},
            'Bad Name': {'connection': 'cloud', 'serial': 'S2'},
            'empty': {'connection': 'bluetooth'},
            'odd': {'connection': 'satellite', 'serial': 'S3'},
            'typed': {'connection': 'cloud', 'serial': 5},
            'listed': ['cloud']}})
    assert [alias.name for alias in aliases.aliases] == ['desk']
    assert aliases.default is None
    assert ChargerAliases.from_document({'version': 2}).aliases == []
    alias = ChargerAlias('lamp', Connection.BLUETOOTH, address='A')
    assert alias.with_serial('S').to_document() == {
        'connection': 'bluetooth', 'serial': 'S', 'address': 'A'}
