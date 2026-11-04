"""Colour decisions, escape-aware widths and coloured tables."""

import io

import pytest

from ankerprime.cli.render import render_table
from ankerprime.cli.style import (
    PLAIN, ColorMode, Painter, truncate_visible, use_color, visible_width)
from tests.utils import Terminal


COLOURS = Painter(True)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ('NO_COLOR', 'FORCE_COLOR', 'TERM'):
        monkeypatch.delenv(name, raising=False)


def test_painter_wraps_only_when_enabled() -> None:
    assert COLOURS.good('on') == '\x1b[32mon\x1b[0m'
    assert COLOURS.bad('x') == '\x1b[1;31mx\x1b[0m'
    assert COLOURS.dim('') == ''
    assert PLAIN.good('on') == 'on' and not PLAIN.enabled


def test_visible_width_and_truncation() -> None:
    text = COLOURS.good('abc') + 'def' + COLOURS.dim('gh')
    assert visible_width(text) == 8
    assert truncate_visible(text, 20) == text
    cut = truncate_visible(text, 4)
    assert visible_width(cut) == 4
    assert cut.startswith('\x1b[32mabc\x1b[0md') and cut.endswith('\x1b[0m')
    assert truncate_visible('plain text', 5) == 'plain\x1b[0m'


@pytest.mark.parametrize(('mode', 'tty', 'environment', 'expected'), [
    (ColorMode.AUTO, True, {}, True),
    (ColorMode.AUTO, False, {}, False),
    (ColorMode.AUTO, True, {'NO_COLOR': '1'}, False),
    (ColorMode.AUTO, True, {'TERM': 'dumb'}, False),
    (ColorMode.AUTO, False, {'FORCE_COLOR': '1'}, True),
    (ColorMode.ALWAYS, False, {}, True),
    (ColorMode.ALWAYS, True, {'NO_COLOR': '1'}, True),
    (ColorMode.NEVER, True, {'FORCE_COLOR': '1'}, False),
])
def test_use_color(monkeypatch: pytest.MonkeyPatch, mode: ColorMode,
                   tty: bool, environment: dict[str, str],
                   expected: bool) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    stream = Terminal() if tty else io.StringIO()
    assert use_color(mode, stream) is expected


def test_coloured_table_keeps_columns_aligned() -> None:
    rows = [(COLOURS.accent('C1'), COLOURS.good('on'), 'x'),
            ('C22', 'off', 'y')]
    lines = render_table(('Port', 'Out', 'Z'), rows, painter=COLOURS)
    plain = [line.replace('\x1b[0m', '') for line in lines.splitlines()]
    stripped = [visible_width(line) for line in lines.splitlines()]
    assert stripped == [len('Port  Out  Z'), len('C1    on   x'),
                        len('C22   off  y')]
    assert plain[0].startswith('\x1b[1;4mPort')
