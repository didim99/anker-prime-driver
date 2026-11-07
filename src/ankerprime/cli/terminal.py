"""Terminal input and output: secrets, in-place redrawn blocks (ANSI)."""

import getpass
import shutil
import sys
from typing import Self, TextIO

from ankerprime.cli.style import truncate_visible


HIDE_CURSOR = '\x1b[?25l'
SHOW_CURSOR = '\x1b[?25h'
CLEAR_BELOW = '\x1b[J'


class TerminalBlock:
    """Replaces its previous text instead of appending below it.

    Only the lines it wrote are cleared, so earlier output and the scrollback
    stay intact. Lines are cut to the terminal width (colours kept), since
    wrapped lines would break the line count.
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
        lines = [truncate_visible(line, size.columns)
                 for line in text.splitlines()]
        lines = lines[:max(size.lines - 1, 1)]
        prefix = f'\x1b[{self._lines}F' if self._lines else ''
        self._out.write(prefix + CLEAR_BELOW + '\n'.join(lines) + '\n')
        self._out.flush()
        self._lines = len(lines)


def read_secret(prompt: str, *, from_stdin: bool, hidden: bool) -> str:
    """A password or a one-time code, typed or piped to standard input.

    Hidden input (a password) is typed unseen and kept as typed apart from
    the line end; visible input (a code) is trimmed.
    """

    if from_stdin:
        line = sys.stdin.readline()
        return line.rstrip('\r\n') if hidden else line.strip()
    if hidden:
        return getpass.getpass(prompt)
    return input(prompt).strip()
