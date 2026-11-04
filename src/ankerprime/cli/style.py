"""Terminal colours: semantic styles on top of ANSI SGR codes.

Colours are on for terminals unless ``NO_COLOR`` is set or ``TERM`` is
``dumb``; ``FORCE_COLOR`` or ``--color always`` turns them on for pipes
too (``less -R``). Rendering code asks for a role (good, bad, ...), never for
a colour, so the palette lives here only.
"""

import os
import re
import sys
from enum import StrEnum
from typing import TextIO

if sys.platform == 'win32':
    import ctypes
    import msvcrt
    from ctypes import wintypes


ANSI_PATTERN = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
RESET = '\x1b[0m'
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


class ColorMode(StrEnum):
    AUTO = 'auto'
    ALWAYS = 'always'
    NEVER = 'never'


class Role(StrEnum):
    """What a piece of text means; mapped to SGR codes in ``PALETTE``."""

    HEADING = 'heading'
    STRONG = 'strong'
    ACCENT = 'accent'
    GOOD = 'good'
    WARN = 'warn'
    BAD = 'bad'
    DIM = 'dim'


PALETTE = {
    Role.HEADING: '1;4',
    Role.STRONG: '1',
    Role.ACCENT: '36',
    Role.GOOD: '32',
    Role.WARN: '33',
    Role.BAD: '1;31',
    Role.DIM: '2',
}


class Painter:
    """Wraps text in the codes of a role, or returns it unchanged."""

    _enabled: bool

    def __init__(self, enabled: bool):
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def paint(self, text: str, role: Role) -> str:
        if not self._enabled or not text:
            return text
        return f'\x1b[{PALETTE[role]}m{text}{RESET}'

    def heading(self, text: str) -> str:
        return self.paint(text, Role.HEADING)

    def strong(self, text: str) -> str:
        return self.paint(text, Role.STRONG)

    def accent(self, text: str) -> str:
        return self.paint(text, Role.ACCENT)

    def good(self, text: str) -> str:
        return self.paint(text, Role.GOOD)

    def warn(self, text: str) -> str:
        return self.paint(text, Role.WARN)

    def bad(self, text: str) -> str:
        return self.paint(text, Role.BAD)

    def dim(self, text: str) -> str:
        return self.paint(text, Role.DIM)


PLAIN = Painter(False)


def visible_width(text: str) -> int:
    """Length without escape sequences."""

    return len(ANSI_PATTERN.sub('', text))


def truncate_visible(text: str, width: int) -> str:
    """Cut to ``width`` visible characters, keeping escape sequences."""

    if visible_width(text) <= width:
        return text
    parts = []
    shown = 0
    position = 0
    for match in ANSI_PATTERN.finditer(text):
        plain = text[position:match.start()]
        take = plain[:max(width - shown, 0)]
        parts.append(take)
        shown += len(take)
        parts.append(match.group())
        position = match.end()
    parts.append(text[position:][:max(width - shown, 0)])
    return ''.join(parts) + RESET


def use_color(mode: ColorMode, stream: TextIO) -> bool:
    """Decide whether to colour output written to ``stream``."""

    if mode == ColorMode.NEVER:
        return False
    if mode == ColorMode.ALWAYS or os.environ.get('FORCE_COLOR'):
        return enable_ansi(stream) or not is_terminal(stream)
    if os.environ.get('NO_COLOR') or os.environ.get('TERM') == 'dumb':
        return False
    return ansi_terminal(stream)


def ansi_terminal(stream: TextIO) -> bool:
    """``stream`` is a terminal that interprets escape sequences."""

    return is_terminal(stream) and enable_ansi(stream)


def enable_ansi(stream: TextIO) -> bool:
    """Make a terminal interpret escape sequences; False when impossible.

    Windows consoles need virtual terminal processing switched on; other
    terminals always interpret them.
    """

    if sys.platform == 'win32':
        if not is_terminal(stream):
            return False
        try:
            handle = msvcrt.get_osfhandle(stream.fileno())
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return bool(kernel32.SetConsoleMode(handle, new_mode))
        except (AttributeError, OSError, ValueError):
            return False
    return True


def is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False
