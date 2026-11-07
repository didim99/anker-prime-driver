"""CLI commands end to end, with a fake Anker cloud and MQTT broker."""

import json
import re
import stat
from pathlib import Path

import pytest

from ankerprime.cli.config import CONFIG_DIR_VARIABLE, default_config_dir
from ankerprime.cli.main import EXIT_AUTHENTICATION, EXIT_ERROR
from ankerprime.model import A2345, Capability, Verification
from tests.cli.harness import Cli
from tests.cloud.fakes import DEVICES, FakeAnkerCloud
from tests.device.charger import charger_responder
from tests.transport.fakes import FakeMqttConnector, charger_over_mqtt


def test_help_without_command(cli: Cli) -> None:
    code, out = cli.run()
    assert code == 0 and 'usage: ankerprime' in out


def test_regions(cli: Cli) -> None:
    code, out = cli.run('regions')
    assert code == 0
    assert re.search(r'^JP +Japan +ankerpower-api-eu\.anker\.com$', out,
                     re.M)
    assert re.search(r'^CN +Mainland China +aiot-api-cn\.anker\.com\.cn '
                     r'\(SMS login: login-phone\)$', out, re.M)


def test_login_stores_owner_only_files(cli: Cli, tmp_path: Path) -> None:
    code, out = cli.run('login', 'a@example.com', 'jp', '--password-stdin')
    assert code == 0 and 'Signed in' in out
    for name in ('credentials.json', 'authentication.json'):
        path = tmp_path / 'config' / name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stored = json.loads((tmp_path / 'config' / 'credentials.json').read_text())
    assert stored['password'] == 'secret' and stored['country'] == 'JP'


def test_login_without_saving_password(cli: Cli) -> None:
    cli.login('--no-save-password')
    assert cli.credentials() is None
    assert cli.authentication() is not None


def test_login_rejects_unknown_country(
        cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('login', 'a@example.com', 'xx', '--password-stdin')
    assert code == EXIT_ERROR
    assert 'unknown country code' in capsys.readouterr().err


def test_wrong_password(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    cli.cloud.set_login_ok(False)
    code, _ = cli.run('login', 'a@example.com', 'jp', '--password-stdin')
    assert code == EXIT_AUTHENTICATION
    assert 'wrong password' in capsys.readouterr().err


def test_commands_need_a_login(cli: Cli,
                               capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('devices')
    assert code == EXIT_AUTHENTICATION
    err = capsys.readouterr().err
    assert 'not signed in' in err and 'ankerprime login' in err


def test_devices_and_logout(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('devices')
    assert code == 0
    assert 'SERIAL123' in out and 'Desk charger' in out and 'A2687' in out
    code, out = cli.run('logout')
    assert code == 0
    assert cli.run('devices')[0] == EXIT_AUTHENTICATION


def test_status_text_and_json(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('status')
    assert code == 0
    assert 'Desk charger (A2345)' in out and 'Mode: custom' in out
    code, out = cli.run('status', '--json')
    assert code == 0
    document = json.loads(out)
    assert document['info']['serial'] == 'SERIAL123'
    assert document['available'] is True


def test_monitor_prints_requested_count(tmp_path: Path,
                                        password: None) -> None:
    streaming = charger_responder(realtime_frames=10)
    connector = FakeMqttConnector(charger_over_mqtt(streaming),
                                  reply_interval=0.02)
    cli = Cli(tmp_path / 'config', FakeAnkerCloud(), connector)
    cli.login()
    code, out = cli.run('monitor', '--json', '--count', '3',
                        '--interval', '0')
    assert code == 0
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)['available'] for line in lines)
    last = json.loads(lines[-1])['energy']
    assert last['c1'] > 0 and last['a2'] == 0.0
    energy_file = tmp_path / 'config' / 'energy-serial123.json'
    saved = json.loads(energy_file.read_text())['counters']
    assert saved['c1'] >= last['c1']
    assert stat.S_IMODE(energy_file.stat().st_mode) == 0o600
    code, out = cli.run('status', '--json')
    assert code == 0
    assert json.loads(out)['energy']['c1'] >= saved['c1']
    assert json.loads(energy_file.read_text())['counters'] == saved


def test_energy_reset(cli: Cli, tmp_path: Path) -> None:
    energy_file = tmp_path / 'config' / 'energy-serial123.json'
    code, out = cli.run('energy-reset', '--serial', 'SERIAL123')
    assert code == 0 and 'No energy counters' in out
    energy_file.write_text(json.dumps({
        'version': 1, 'counters': {'c1': 2.5, 'c2': 1.0, 'a1': 0.5}}))
    code, out = cli.run('energy-reset', 'c1', 'A1', '--serial', 'serial123')
    assert code == 0
    assert out.splitlines() == ['Energy counters reset: c1, a1.',
                                'A running monitor follows within a minute.']
    document = json.loads(energy_file.read_text())
    assert document['counters'] == {'c1': 0.0, 'c2': 1.0, 'a1': 0.0}
    assert set(document['resets']) == {'c1', 'a1'}
    cli.login()
    code, out = cli.run('energy-reset')
    assert code == 0 and 'reset: c1, c2, a1.' in out
    assert json.loads(energy_file.read_text())['counters'] == {
        'c1': 0.0, 'c2': 0.0, 'a1': 0.0}


def test_energy_reset_rejects_unknown_ports(cli: Cli) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.run('energy-reset', 'c9', '--serial', 'SERIAL123')
    assert raised.value.code == 2


@pytest.mark.parametrize(('detail', 'shows_settings'), [
    ((), False), (('--detail',), True),
])
def test_monitor_redraws_in_place_on_a_terminal(
        cli: Cli, detail: tuple[str, ...], shows_settings: bool) -> None:
    cli.login()
    code, out = cli.run('monitor', '--count', '2', '--interval', '0',
                        *detail, tty=True)
    assert code == 0
    assert out.startswith('\x1b[?25l\x1b[J') and out.endswith('\x1b[?25h')
    views = out.split('F\x1b[J')
    assert len(views) == 2
    assert all('Running since ' in view and 'Updated ' in view
               and 'C1 ' in view for view in views)
    assert ('Mode:' in out) is shows_settings


def test_colours_on_request(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('status', color='always')
    assert code == 0
    assert '\x1b[32mlive\x1b[0m' in out
    assert '\x1b[1;4mPort\x1b[0m' in out
    code, out = cli.run('output', 'c1', 'on', color='always')
    assert out.strip() == '\x1b[32mc1 switched on.\x1b[0m'
    code, out = cli.run('status', '--json', color='always')
    assert '\x1b[' not in out


def test_verified_write_prints_confirmation_only(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('output', 'c1', 'on')
    assert code == 0
    assert out.strip() == 'c1 switched on.'
    published = [json.loads(payload)
                 for _, payload in cli.connector.last.published]
    assert len(published) >= 4


def test_experimental_write_asks_for_verification(
        cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    capabilities = dict(A2345.capabilities)
    capabilities[Capability.MAX_COMPATIBILITY] = Verification.EXPERIMENTAL
    monkeypatch.setattr(A2345, '_capabilities', capabilities)
    cli.login()
    code, out = cli.run('switch', 'max-compatibility', 'on')
    assert code == 0
    assert 'max-compatibility switched on.' in out
    assert 'max_compatibility is not yet verified' in out


def test_countdown_start_and_cancel(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('countdown', 'usb-a', '15m')
    assert code == 0
    assert out.strip() == 'usb_a switches off in 900 s.'
    code, out = cli.run('countdown', 'c2', 'cancel')
    assert code == 0 and 'c2 countdown cancelled.' in out


@pytest.mark.parametrize('argv', [
    ('schedule', 'c2', 'on', '07:30', '--days', 'weekdays'),
    ('mode', 'port_priority'),
    ('priority', 'c1', 'c3'),
    ('display', '--brightness', '60', '--screen-timeout', '5min',
     '--knob', 'forward', '--hours', '12h'),
    ('clock', 'on', '--theme', 'style_3'),
    ('clock-schedule', '22:00', '07:00', '--days', 'daily'),
    ('holiday', 'off'),
    ('switch', 'max-compatibility', 'on'),
])
def test_control_commands(cli: Cli, argv: tuple[str, ...]) -> None:
    cli.login()
    code, out = cli.run(*argv)
    assert code == 0, out
    assert out.strip()


def test_unsupported_and_invalid_writes(
        cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    cli.login()
    assert cli.run('allocation', 'dual_laptop')[0] == EXIT_ERROR
    assert 'not supported: fixed_allocation' in capsys.readouterr().err
    assert cli.run('display', '--brightness', '22')[0] == EXIT_ERROR
    assert 'brightness out of range' in capsys.readouterr().err
    assert cli.run('switch', 'ufcs', 'on')[0] == EXIT_ERROR
    assert cli.run('display')[0] == EXIT_ERROR
    assert 'at least one display option' in capsys.readouterr().err


def test_serial_selection(tmp_path: Path, password: None,
                          capsys: pytest.CaptureFixture[str]) -> None:
    devices = [*DEVICES, {'device_sn': 'SERIAL789', 'product_code': 'A2345'}]
    connector = FakeMqttConnector(charger_over_mqtt(charger_responder()))
    cli = Cli(tmp_path / 'config', FakeAnkerCloud(devices), connector)
    cli.login()
    assert cli.run('status')[0] == EXIT_ERROR
    assert 'SERIAL123, SERIAL789' in capsys.readouterr().err
    assert cli.run('status', '--serial', 'serial789')[0] == 0
    assert cli.run('status', '--serial', 'SERIAL456')[0] == EXIT_ERROR


def test_silent_charger_times_out(tmp_path: Path, password: None,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(tmp_path / 'config', FakeAnkerCloud(), FakeMqttConnector())
    cli.login()
    assert cli.run('status', '--timeout', '0.2')[0] == EXIT_ERROR
    assert 'no charger state within' in capsys.readouterr().err


def test_refreshed_token_is_saved(cli: Cli) -> None:
    cli.login()
    cli.cloud.revoke('token-1')
    assert cli.run('devices')[0] == 0
    authentication = cli.authentication()
    assert authentication is not None
    assert authentication.auth_token == 'token-2'


def test_default_config_dir(monkeypatch: pytest.MonkeyPatch,
                            tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_DIR_VARIABLE, str(tmp_path / 'custom'))
    assert default_config_dir() == tmp_path / 'custom'
    monkeypatch.delenv(CONFIG_DIR_VARIABLE)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    assert default_config_dir() == tmp_path / 'xdg' / 'ankerprime'
