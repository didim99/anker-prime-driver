"""Coercion of Anker's JSON values."""

import pytest

from ankerprime.cloud.values import (
    flexible_bool, flexible_int, hex_u32,
    optional_bool, optional_str, prefixed_u32)


@pytest.mark.parametrize('value, expected', [
    (1, 1), ('2', 2), (True, 1), (3.0, 3), (2.5, None), ('x', None),
    (None, None),
])
def test_flexible_int(value: object, expected: int | None) -> None:
    assert flexible_int(value) == expected


def test_flexible_bool() -> None:
    assert flexible_bool('1') is True
    assert flexible_bool(0) is False
    assert flexible_bool(None) is None


@pytest.mark.parametrize('value, expected', [
    ('0x1A', 0x1A), ('1a', 0x1A), (26, 26), ('zz', None), (True, None),
])
def test_hex_u32(value: object, expected: int | None) -> None:
    assert hex_u32(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('0x10', 16), ('10', 10), (7, 7), ('x', None), (None, None),
])
def test_prefixed_u32(value: object, expected: int | None) -> None:
    assert prefixed_u32(value) == expected


def test_optional_values_keep_their_type() -> None:
    item = {'name': 'x', 'flag': True, 'number': 1}
    assert optional_str(item, 'name') == 'x'
    assert optional_str(item, 'number') is None
    assert optional_bool(item, 'flag') is True
    assert optional_bool(item, 'number') is None
