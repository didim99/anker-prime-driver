"""Coercion of the loosely typed values in Anker's JSON responses."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


MILLISECOND_EPOCH_THRESHOLD = 100_000_000_000


def flexible_int(value: object) -> int | None:
    """Anker sends the same number as an int, a string or a bool."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def flexible_bool(value: object) -> bool | None:
    """1 is true, every other number false."""

    number = flexible_int(value)
    return None if number is None else number == 1


def hex_u32(value: object) -> int | None:
    """``"0x1a2b3c4d"``, ``"1a2b3c4d"`` or a number."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if not isinstance(value, str):
        return None
    text = value.strip().lower().removeprefix('0x')
    try:
        return int(text, 16) & 0xFFFFFFFF
    except ValueError:
        return None


def prefixed_u32(value: object) -> int | None:
    """Hex with a ``0x`` prefix, decimal without, or a number."""

    if isinstance(value, str) and not value.strip().lower().startswith('0x'):
        try:
            return int(value.strip()) & 0xFFFFFFFF
        except ValueError:
            return None
    return hex_u32(value)


def optional_str(item: Mapping[str, Any], key: str) -> str | None:
    """The value of a key when it is a string."""

    value = item.get(key)
    return value if isinstance(value, str) else None


def optional_bool(item: Mapping[str, Any], key: str) -> bool | None:
    """The value of a key when it is a JSON boolean."""

    value = item.get(key)
    return value if isinstance(value, bool) else None


def parse_expiration(value: object) -> datetime | None:
    """Epoch seconds or milliseconds, as a number or string."""

    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not seconds > 0 or seconds == float('inf'):
        return None
    if seconds > MILLISECOND_EPOCH_THRESHOLD:
        seconds /= 1000
    return datetime.fromtimestamp(seconds, UTC)
