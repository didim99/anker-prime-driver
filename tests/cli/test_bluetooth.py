"""CLI over Bluetooth, with the simulated 160W in place of the radio."""

import io
import json
from functools import partial
from pathlib import Path

import pytest
from bleak.backends.device import BLEDevice

from ankerprime.cli.main import EXIT_AUTHENTICATION, main
from ankerprime.device import a2687
from ankerprime.device.a2687 import A2687Timings
from ankerprime.interfaces.channel import Channel
from ankerprime.transport.ble import DiscoveredCharger
from tests.cloud.fakes import FakeAnkerCloud
from tests.device.a2687_charger import SimulatedCharger, SimulatedLink
from tests.utils import Terminal


ADDRESS = 'AA:BB:CC:DD:EE:FF'


class BluetoothCli:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.charger = SimulatedCharger()
        self.found: list[DiscoveredCharger] = []
        self.addresses: list[str] = []

    def run(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        code = main(['--config-dir', str(self.config_dir), '--color',
                     'never', *argv],
                    ble_transport=self._link, ble_scanner=self._scan,
                    out=out)
        return code, out.getvalue()

    def login(self) -> None:
        from tests.cli.test_commands import Cli
        from tests.transport.fakes import FakeMqttConnector
        cli = Cli(self.config_dir, FakeAnkerCloud(), FakeMqttConnector(None))
        cli.login()

    def _link(self, address: str) -> Channel:
        self.addresses.append(address)
        return SimulatedLink(self.charger)

    async def _scan(self, timeout: float) -> list[DiscoveredCharger]:
        return self.found


@pytest.fixture
def cli(tmp_path: Path) -> BluetoothCli:
    return BluetoothCli(tmp_path / 'config')


def test_scan(cli: BluetoothCli) -> None:
    code, out = cli.run('scan', '--timeout', '1')
    assert code == 0 and 'No chargers found' in out
    cli.found = [
        DiscoveredCharger(BLEDevice(ADDRESS, 'ASHDJW1', None), 'ASHDJW1', -60,
                          True),
        DiscoveredCharger(BLEDevice('00:00:00:00:00:01', None, None), None,
                          -70, False)]
    code, out = cli.run('scan')
    assert code == 0
    lines = out.splitlines()
    assert lines[1].split() == ['Address', 'Name', 'Signal', 'Match']
    assert lines[2].split() == [ADDRESS, 'ASHDJW1', '-60', 'dBm', 'A2687']
    assert lines[3].split() == ['00:00:00:00:00:01', '-', '-70', 'dBm',
                                'possible']


def test_status_over_bluetooth(cli: BluetoothCli) -> None:
    code, out = cli.run('status', '--ble', ADDRESS, '--country', 'de')
    assert code == 0
    assert 'Anker Prime 160W (A2687)' in out
    assert 'firmware v0.0.5.0' in out
    assert 'C1' in out and '65.00 W' in out
    assert 'brightness 80%' in out
    assert cli.addresses == [ADDRESS]
    probe = dict(cli.charger.requests)[0x020A]
    assert probe.get(0xA2) == b'\x04DE'


def test_client_id_is_kept_between_runs(cli: BluetoothCli) -> None:
    assert cli.run('status', '--ble', ADDRESS)[0] == 0
    first = cli.charger.identity
    stored = json.loads((cli.config_dir / 'bluetooth.json').read_text())
    assert stored['client_id'] == first
    assert cli.run('status', '--ble', ADDRESS)[0] == 0
    assert cli.charger.identity == first


def test_owner_id_defaults_to_the_signed_in_account(
        cli: BluetoothCli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('sys.stdin', io.StringIO('secret\n'))
    cli.login()
    cli.charger.required_identity = 'u' * 40
    assert cli.run('status', '--ble', ADDRESS)[0] == 0
    cli.charger.required_identity = 'other'
    assert cli.run('status', '--ble', ADDRESS, '--owner-id', 'other')[0] == 0


def test_refused_identity_hints_at_the_owner_id(
        cli: BluetoothCli, capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a2687, 'A2687Timings',
                        partial(A2687Timings, telemetry_deadline=0.2))
    cli.charger.required_identity = 'owner'
    code, _ = cli.run('status', '--ble', ADDRESS, '--timeout', '5')
    assert code == EXIT_AUTHENTICATION
    err = capsys.readouterr().err
    assert 'refused the user identity' in err and '--owner-id' in err


def test_commands_over_bluetooth(cli: BluetoothCli) -> None:
    code, out = cli.run('output', '--ble', ADDRESS, 'c3', 'on')
    assert code == 0 and cli.charger.ports[2].on
    code, out = cli.run('display', '--ble', ADDRESS, '--brightness', '40',
                        '--language', 'de')
    assert code == 0
    assert cli.charger.settings[0xA9] == 40 and cli.charger.language == 3
    code, out = cli.run('allocation', '--ble', ADDRESS, 'dual_laptop')
    assert code == 0 and cli.charger.allocation == 1


def test_serial_and_ble_exclude_each_other(cli: BluetoothCli) -> None:
    with pytest.raises(SystemExit):
        cli.run('status', '--ble', ADDRESS, '--serial', 'X')


def test_monitor_over_bluetooth(cli: BluetoothCli) -> None:
    out = Terminal()
    code = main(['--config-dir', str(cli.config_dir), '--color', 'never',
                 'monitor', '--ble', ADDRESS, '--json', '--count', '2'],
                ble_transport=lambda address: SimulatedLink(cli.charger),
                out=out)
    assert code == 0
    states = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(states) == 2
    assert states[0]['product']['code'] == 'A2687'
