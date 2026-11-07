"""CLI screen-picture and custom-mode set over Bluetooth (160W)."""

import importlib
from functools import partial
from pathlib import Path

import pytest
from PIL import Image

from ankerprime.cli.main import EXIT_ERROR
from ankerprime.device import a2687
from ankerprime.device.a2687 import A2687Timings, ScreenPictureTimings
from tests.cli.test_bluetooth import ADDRESS, BluetoothCli


FAST = ScreenPictureTimings(settle_after_select=0.0, settle_after_start=0.0,
                            chunk_spacing=0.0, settle_before_verify=0.0,
                            verify_delay=0.01)


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BluetoothCli:
    # Imported first: its annotations use the class about to be replaced.
    importlib.import_module('ankerprime.api.ble')
    monkeypatch.setattr(a2687, 'A2687Timings',
                        partial(A2687Timings, screen_picture=FAST))
    cli = BluetoothCli(tmp_path / 'config')
    assert cli.run('charger', 'add', 'desk', '--ble', ADDRESS,
                   '--default')[0] == 0
    return cli


def picture_file(path: Path) -> Path:
    Image.new('RGB', (400, 300), (10, 120, 200)).save(path, format='PNG')
    return path


def test_upload_needs_the_confirmation(cli: BluetoothCli, tmp_path: Path,
                                       capsys: pytest.CaptureFixture[str]
                                       ) -> None:
    path = picture_file(tmp_path / 'p.png')
    code, out = cli.run('screen-picture', 'upload', str(path))
    assert code == EXIT_ERROR
    assert 'no Bluetooth command removes it' in out
    assert '--i-understand' in capsys.readouterr().err
    assert cli.charger.stored_pictures == {}


def test_upload_and_select(cli: BluetoothCli, tmp_path: Path) -> None:
    path = picture_file(tmp_path / 'p.png')
    code, out = cli.run('screen-picture', 'upload', str(path),
                        '--i-understand')
    assert code == 0, out
    assert 'Screen picture shown: id' in out
    assert 'not yet verified' in out
    ((picture_id, crc),) = cli.charger.stored_pictures.items()
    assert f'CRC-32 {crc:08x}' in out
    assert cli.charger.screen_picture == picture_id & 0xFFFF
    cli.charger.screen_picture = 1
    code, out = cli.run('screen-picture', 'select', str(picture_id),
                        f'{crc:x}')
    assert code == 0 and 'shown' in out
    assert cli.charger.screen_picture == picture_id & 0xFFFF


def test_failed_upload_says_what_is_left(cli: BluetoothCli, tmp_path: Path
                                         ) -> None:
    cli.charger.cover_hide = True
    path = picture_file(tmp_path / 'p.png')
    code, out = cli.run('screen-picture', 'upload', str(path),
                        '--i-understand')
    assert code == EXIT_ERROR
    assert 'in the charger now' in out


def test_custom_mode_set(cli: BluetoothCli) -> None:
    code, out = cli.run('custom-mode')
    assert code == 0 and 'keeps no profiles' in out
    code, out = cli.run('custom-mode', 'set', '--c1', '100', '--c3', '0')
    assert code == 0
    assert 'Custom mode set: C1 100 W, C2 60 W, C3 0 W.' in out
    assert cli.charger.custom_power == bytes((1, 0, 100, 60, 0))
    code, _ = cli.run('custom-mode', 'set', '--c4', '20')
    assert code == EXIT_ERROR
    code, out = cli.run('custom-mode', 'exit')
    assert code == 0 and cli.charger.settings[0xAA] == 0
