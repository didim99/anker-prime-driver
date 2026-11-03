"""Packaging smoke tests."""

from importlib import import_module
from importlib.metadata import version

import pytest

from ankerprime import __version__
from ankerprime.cli.main import main


SUBPACKAGES = [
    'api', 'cli', 'cloud', 'device', 'model', 'protocol', 'protocol.a2345',
    'protocol.a2687', 'session', 'storage', 'transport',
]


def test_version_matches_metadata() -> None:
    assert __version__ == version('anker-prime-driver')


@pytest.mark.parametrize('name', SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    import_module(f'ankerprime.{name}')


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(['--version'])
    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_without_arguments_prints_help(
        capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert 'usage: ankerprime' in capsys.readouterr().out
