"""CLI login-phone for mainland China accounts."""

import io
import json
from pathlib import Path

import pytest

from ankerprime.cli.main import EXIT_AUTHENTICATION, EXIT_ERROR
from tests.cli.test_commands import Cli
from tests.cloud.fakes import SMS_CODE, FakeAnkerCloud
from tests.device.charger import charger_responder
from tests.transport.fakes import FakeMqttConnector, charger_over_mqtt


PHONE = '13800138000'


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    connector = FakeMqttConnector(charger_over_mqtt(charger_responder()))
    return Cli(tmp_path / 'config', FakeAnkerCloud(), connector)


def stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr('sys.stdin', io.StringIO(text))


def test_sends_the_code_and_signs_in(cli: Cli, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    stdin(monkeypatch, f'{SMS_CODE}\n')
    code, out = cli.run('login-phone', PHONE, '--code-stdin')
    assert code == 0
    assert f'SMS code sent to {PHONE}.' in out
    assert 'aiot-api-cn.anker.com.cn' in out
    assert cli.cloud.sms_codes == [PHONE]
    authentication = cli.authentication()
    assert authentication is not None and authentication.country == 'CN'
    # Only the token is stored: there is no password to keep.
    assert cli.credentials() is None
    assert not (tmp_path / 'config' / 'credentials.json').exists()
    code, out = cli.run('devices')
    assert code == 0 and 'SERIAL123' in out


def test_two_steps_for_scripts(cli: Cli) -> None:
    code, out = cli.run('login-phone', PHONE, '--send-only')
    assert code == 0 and 'sent' in out
    assert cli.authentication() is None
    code, out = cli.run('login-phone', PHONE, '--code', SMS_CODE)
    assert code == 0 and 'Signed in' in out
    assert cli.cloud.sms_codes == [PHONE]


def test_wrong_code(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('login-phone', PHONE, '--code', '000000')
    assert code == EXIT_AUTHENTICATION
    assert 'login-phone' in capsys.readouterr().err


def test_invalid_number(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('login-phone', '12345', '--send-only')
    assert code == EXIT_ERROR
    assert '11 digits' in capsys.readouterr().err
    assert cli.cloud.sms_codes == []


def test_email_login_points_to_the_phone(
        cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = cli.run('login', 'a@example.com', 'cn', '--password-stdin')
    assert code == EXIT_ERROR
    assert 'login-phone' in capsys.readouterr().err


def test_stored_mainland_token_is_reused(cli: Cli, tmp_path: Path) -> None:
    assert cli.run('login-phone', PHONE, '--code', SMS_CODE)[0] == 0
    stored = json.loads((tmp_path / 'config' / 'authentication.json')
                        .read_text())
    assert stored['server'] == 'https://aiot-api-cn.anker.com.cn'
    code, out = cli.run('status')
    assert code == 0 and 'Desk charger (A2345)' in out
