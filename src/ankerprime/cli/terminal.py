"""In-place redrawing of a block of terminal lines (ANSI escapes)."""

import shutil
from typing import Self, TextIO


HIDE_CURSOR = '\x1b[?25l'
SHOW_CURSOR = '\x1b[?25h'
CLEAR_BELOW = '\x1b[J'


class TerminalBlock:
    """Replaces its previous text instead of appending below it.

    Only the lines it wrote are cleared, so earlier output and the scrollback
    stay intact. Lines are cut to the terminal width, since wrapped lines
    would break the line count.
    """

    _out: TextIO
    _lines: int

    def __init__(self, out: TextIO):
        self._out = out
        self._lines = 0

    def __enter__(self) -> Self:
        self._out.write(HIDE_CURSOR)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._out.write(SHOW_CURSOR)
        self._out.flush()

    def show(self, text: str) -> None:
        size = shutil.get_terminal_size()
        lines = [line[:size.columns] for line in text.splitlines()]
        lines = lines[:max(size.lines - 1, 1)]
        prefix = f'\x1b[{self._lines}F' if self._lines else ''
        self._out.write(prefix + CLEAR_BELOW + '\n'.join(lines) + '\n')
        self._out.flush()
        self._lines = len(lines)
