"""CLI custom-mode and labs commands against the fake cloud and broker."""

import argparse
import base64
import json

import pytest

from ankerprime.cli.commands.custom import parse_port_protocols
from ankerprime.cli.main import EXIT_ERROR
from ankerprime.model import CustomProtocol, OutputId
from tests.cli.harness import Cli


def sent_types(cli: Cli) -> list[int]:
    types = []
    for _, payload in cli.connector.last.published:
        envelope = json.loads(payload)
        frame = base64.b64decode(json.loads(envelope['payload'])['data'])
        types.append(int.from_bytes(frame[7:9], 'big'))
    return types


def test_parse_port_protocols() -> None:
    output, protocols = parse_port_protocols('C2=ufcs,pps-11v')
    assert output == OutputId.C2
    assert protocols == {CustomProtocol.UFCS, CustomProtocol.PPS_11V}
    assert parse_port_protocols('a=') == (OutputId.USB_A, frozenset())
    with pytest.raises(argparse.ArgumentTypeError):
        parse_port_protocols('c1')
    with pytest.raises(argparse.ArgumentTypeError):
        parse_port_protocols('c1=qc3')


def test_add_list_show_edit_delete(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('custom-mode')
    assert code == 0 and 'No stored profiles' in out
    assert 'setting is off' in out

    code, out = cli.run('custom-mode', 'add', '2', 'Desk', '--c1', '60',
                        '--protocols', 'c1=ufcs,pd_12v', '--auto-exit', 'on')
    assert code == 0 and 'Profile 2 stored.' in out
    assert 'not yet verified' in out
    stored = list(cli.cloud.mini_power.profiles.values())
    assert len(stored) == 1
    body = stored[0]
    assert body['name'] == 'Desk' and body['auto_exit'] == 1
    c1 = body['power_settings'][0]
    assert c1['power'] == 60 and c1['ufcs'] == 1 and c1['pd12v'] == 1
    assert c1['scp'] == 0

    code, out = cli.run('custom-mode', 'list')
    assert code == 0
    assert '* ' not in out
    row = next(line for line in out.splitlines() if 'Desk' in line)
    assert '60 W' in row and '210 W' in row

    code, out = cli.run('custom-mode', 'show', '2')
    assert code == 0
    assert 'Profile 2: Desk' in out
    assert 'UFCS, PD 12V' in out

    code, out = cli.run('custom-mode', 'edit', '2', '--name', 'Desk B',
                        '--usb-a', '24')
    assert code == 0 and 'Profile 2 changed.' in out
    assert body['name'] == 'Desk B'
    assert body['power_settings'][4]['power'] == 24

    code, _ = cli.run('custom-mode', 'add', '2', 'Again')
    assert code == EXIT_ERROR

    code, out = cli.run('custom-mode', 'delete', '2')
    assert code == 0 and 'Profile 2 deleted.' in out
    assert cli.cloud.mini_power.profiles == {}


def test_invalid_limit_is_refused(cli: Cli,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    cli.login()
    code, _ = cli.run('custom-mode', 'add', '1', 'Big', '--c2', '110')
    assert code == EXIT_ERROR
    assert 'c2 accepts 0 or 15–100 W' in capsys.readouterr().err
    assert cli.cloud.mini_power.profiles == {}


def test_apply_and_exit(cli: Cli) -> None:
    cli.login()
    assert cli.run('custom-mode', 'add', '3', 'Travel')[0] == 0
    code, out = cli.run('custom-mode', 'apply', '3')
    assert code == 0 and 'Profile 3 "Travel" applied.' in out
    assert 0x0206 in sent_types(cli)
    code, out = cli.run('custom-mode', 'exit')
    assert code == 0 and 'Custom mode left.' in out
    assert 0x0211 in sent_types(cli)
    code, _ = cli.run('custom-mode', 'apply', '4')
    assert code == EXIT_ERROR


def test_labs(cli: Cli) -> None:
    cli.login()
    code, out = cli.run('labs')
    assert code == 0 and 'Custom mode setting: off' in out
    code, out = cli.run('labs', 'on')
    assert code == 0 and 'switched on' in out
    settings = cli.cloud.mini_power.settings('SERIAL123')
    assert settings['charging_mode_status'] == 1
    code, out = cli.run('status')
    assert code == 0
    assert 'custom mode setting on' in out
    assert 'profile 1' in out
