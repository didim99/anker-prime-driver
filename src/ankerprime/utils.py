"""Helpers and type aliases shared by several modules."""

import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeVar

from ankerprime.model.errors import ProtocolError


Clock = Callable[[], datetime]
T = TypeVar('T')

REDACT_VISIBLE_SUFFIX = 4
U32_MASK = 0xFFFFFFFF


def utc_now() -> datetime:
    """Default ``Clock``: the current time in UTC."""

    return datetime.now(UTC)


def monotonic() -> float:
    """The running event loop's monotonic clock, in seconds."""

    return asyncio.get_running_loop().time()


def parse_json_object(body: bytes) -> dict[str, Any]:
    """Decode a JSON object; anything else raises ``ProtocolError``."""

    try:
        root = json.loads(body)
    except ValueError:
        raise ProtocolError('malformed JSON from the Anker cloud') from None
    if not isinstance(root, dict):
        raise ProtocolError('unexpected JSON from the Anker cloud')
    return root


def enum_value(member: StrEnum | None) -> str | None:
    """String value of an optional enum member."""

    return member.value if member is not None else None


def u32le(value: int) -> bytes:
    """Four little-endian bytes of the value's low 32 bits."""

    return (value & U32_MASK).to_bytes(4, 'little')


def redact(value: str | None) -> str:
    """Keep only the last four characters of an identifier for logs."""

    if not value:
        return '<none>'
    if len(value) <= REDACT_VISIBLE_SUFFIX:
        return '***'
    return f'***{value[-REDACT_VISIBLE_SUFFIX:]}'


def copy_with(value: T, changes: Mapping[str, Any]) -> T:
    """A copy built from the private fields, with some of them changed.

    For value classes whose keyword-only constructor takes every field
    under its name without the underscore. An unknown name raises
    ``TypeError`` as the constructor does.
    """

    fields = {name.removeprefix('_'): field
              for name, field in vars(value).items()}
    return type(value)(**{**fields, **changes})
