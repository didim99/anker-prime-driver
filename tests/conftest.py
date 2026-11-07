"""Command-line options of the test suite.

Options must be declared in a top-level conftest; the hardware suite under
``tests/hardware`` is collected only with ``--hardware``.
"""

from pathlib import Path

import pytest


HARDWARE_DIRECTORY = Path(__file__).parent / 'hardware'
DEFAULT_SETTLE = 4.0


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('hardware', 'tests on a real 250W')
    group.addoption('--hardware', action='store_true',
                    help='run tests/hardware against the charger of the '
                         "CLI's stored login (changes and restores settings)")
    group.addoption('--hardware-serial', metavar='SERIAL',
                    help="charger serial (default: the CLI's default cloud "
                         'charger, else the only one)')
    group.addoption('--hardware-settle', metavar='SECONDS', type=float,
                    default=DEFAULT_SETTLE,
                    help='seconds to wait for a read-back snapshot '
                         f'(default {DEFAULT_SETTLE:g})')


def pytest_ignore_collect(collection_path: Path,
                          config: pytest.Config) -> bool | None:
    """Leave the hardware suite out of a normal run."""

    if config.getoption('hardware'):
        return None
    if collection_path == HARDWARE_DIRECTORY:
        return True
    return None


def pytest_collection_modifyitems(config: pytest.Config,
                                  items: list[pytest.Item]) -> None:
    """Skip hardware tests named directly without ``--hardware``."""

    if config.getoption('hardware'):
        return
    skip = pytest.mark.skip(reason='needs --hardware')
    for item in items:
        if HARDWARE_DIRECTORY in item.path.parents:
            item.add_marker(skip)
