"""Helpers for the wire-code tables of every protocol."""

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

from ankerprime.model.errors import InvalidParameterError


EnumT = TypeVar('EnumT', bound=Enum)
ValueT = TypeVar('ValueT')


def encode_value(codes: Mapping[EnumT, int], value: EnumT) -> int:
    """Return the wire code of a value; raises when the charger lacks it."""

    if value not in codes:
        raise InvalidParameterError(f'charger does not support {value}')
    return codes[value]


def decode_value(codes: Mapping[EnumT, int], code: int | None) -> EnumT | None:
    """Return the value of a wire code, None for unknown codes."""

    for value, value_code in codes.items():
        if value_code == code:
            return value
    return None


def lookup(table: Mapping[int, ValueT], code: int | None) -> ValueT | None:
    """Return the entry of a code-keyed table, None for unknown codes."""

    return table.get(code) if code is not None else None
