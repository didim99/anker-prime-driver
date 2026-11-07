"""CLI clock-picture command against the fake cloud and broker."""

import io
from pathlib import Path

import pytest
from PIL import Image

from ankerprime.cli.main import EXIT_ERROR
from tests.cli.harness import Cli
from tests.cloud.fakes import FakeAnkerCloud
from tests.device.test_a2345_pictures import reporting_responder
from tests.transport.fakes import FakeMqttConnector, charger_over_mqtt


@pytest.fixture
def cli(tmp_path: Path, password: None) -> Cli:
    responder = charger_over_mqtt(reporting_responder(1))
    cli = Cli(tmp_path / 'config', FakeAnkerCloud(),
              FakeMqttConnector(responder))
    cli.login()
    return cli


def picture_file(path: Path) -> Path:
    image = Image.merge('RGB', [Image.effect_noise((640, 480), 60)
                                for _ in range(3)])
    image.save(path, format='PNG')
    return path


def test_catalogue_and_apply(cli: Cli) -> None:
    code, out = cli.run('clock-picture')
    assert code == 0
    rows = [line.split() for line in out.splitlines()[1:]]
    assert rows == [['31', 'Forest', 'Nature'], ['32', 'Sea', 'Nature'],
                    ['34', 'Night', 'City']]
    code, out = cli.run('clock-picture', 'apply', '32')
    assert code == 0
    assert 'Picture shown; clock switched on.' in out
    assert 'not yet verified' in out
    code, _ = cli.run('clock-picture', 'apply', '99')
    assert code == EXIT_ERROR


def test_own_pictures(cli: Cli, tmp_path: Path) -> None:
    code, out = cli.run('clock-picture', 'list', '--own')
    assert code == 0 and 'No own pictures' in out
    path = picture_file(tmp_path / 'photo.png')
    code, out = cli.run('clock-picture', 'upload', str(path))
    assert code == 0 and 'Picture uploaded' in out
    upload = cli.cloud.mini_power.uploads[0]
    image = Image.open(io.BytesIO(upload))
    assert image.format == 'JPEG' and image.size == (480, 200)

    code, out = cli.run('clock-picture', 'list', '--own')
    assert code == 0 and '1 of 8 own pictures' in out
    picture_id = next(iter(cli.cloud.mini_power.pictures))
    code, out = cli.run('clock-picture', 'apply', '--own', str(picture_id))
    assert code == 0 and 'Picture shown' in out
    code, out = cli.run('clock-picture', 'delete', str(picture_id))
    assert code == 0 and 'deleted' in out
    assert cli.cloud.mini_power.pictures == {}


def test_upload_rejects_non_images(cli: Cli, tmp_path: Path,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / 'notes.txt'
    path.write_text('hello')
    code, _ = cli.run('clock-picture', 'upload', str(path))
    assert code == EXIT_ERROR
    assert 'not a readable image' in capsys.readouterr().err


def test_time_over_own_pictures(cli: Cli) -> None:
    code, out = cli.run('clock-picture', 'time', 'off')
    assert code == 0 and 'switched off' in out
