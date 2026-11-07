"""Argument converters for the CLI; they raise ``ArgumentTypeError``."""

import argparse
import re
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

from ankerprime.cloud.values import hex_u32
from ankerprime.model.enums import OutputId, PortId, Weekday


EnumT = TypeVar('EnumT', bound=StrEnum)

DAY_NAMES = {'mon': Weekday.MONDAY, 'tue': Weekday.TUESDAY,
             'wed': Weekday.WEDNESDAY, 'thu': Weekday.THURSDAY,
             'fri': Weekday.FRIDAY, 'sat': Weekday.SATURDAY,
             'sun': Weekday.SUNDAY}
DAY_ALIASES = {'daily': Weekday.EVERY_DAY, 'once': Weekday.ONCE,
               'weekdays': (Weekday.MONDAY | Weekday.TUESDAY
                            | Weekday.WEDNESDAY | Weekday.THURSDAY
                            | Weekday.FRIDAY),
               'weekend': Weekday.SATURDAY | Weekday.SUNDAY}
OUTPUT_ALIASES = {'a': OutputId.USB_A, 'usb-a': OutputId.USB_A,
                  'usba': OutputId.USB_A}
SWITCH_VALUES = {'on': True, 'off': False}
DURATION_PATTERN = re.compile(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?')


def parse_output(text: str) -> OutputId:
    """``c1``…``c4`` or ``usb_a`` (also ``a``, ``usb-a``), any case."""

    key = text.strip().lower()
    if key in OUTPUT_ALIASES:
        return OUTPUT_ALIASES[key]
    try:
        return OutputId(key)
    except ValueError:
        choices = ', '.join(output.value for output in OutputId)
        raise argparse.ArgumentTypeError(
            f'unknown output {text!r}; choose from {choices}') from None


def parse_port(text: str) -> PortId:
    """``c1``…``c4``, ``a1`` or ``a2``, any case."""

    try:
        return PortId(text.strip().lower())
    except ValueError:
        choices = ', '.join(port.value for port in PortId)
        raise argparse.ArgumentTypeError(
            f'unknown port {text!r}; choose from {choices}') from None


def parse_switch(text: str) -> bool:
    key = text.strip().lower()
    if key not in SWITCH_VALUES:
        raise argparse.ArgumentTypeError(f'expected on or off, got {text!r}')
    return SWITCH_VALUES[key]


def parse_time(text: str) -> tuple[int, int]:
    """``HH:MM`` in 24-hour format."""

    try:
        hour_text, minute_text = text.strip().split(':')
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f'expected HH:MM, got {text!r}') from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise argparse.ArgumentTypeError(f'invalid time {text!r}')
    return hour, minute


def parse_duration(text: str) -> int:
    """Seconds from ``90``, ``15m``, ``2h`` or ``1h30m``."""

    match = DURATION_PATTERN.fullmatch(text.strip().lower())
    if match is None or not any(match.groups()):
        raise argparse.ArgumentTypeError(f'invalid duration {text!r}')
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_countdown(text: str) -> int | None:
    """A duration, or None for ``cancel``."""

    if text.strip().lower() == 'cancel':
        return None
    return parse_duration(text)


def parse_days(text: str) -> Weekday:
    """Comma-separated day names, or daily, weekdays, weekend, once."""

    key = text.strip().lower()
    if key in DAY_ALIASES:
        return DAY_ALIASES[key]
    days = Weekday.ONCE
    for name in key.split(','):
        day = DAY_NAMES.get(name.strip()[:3])
        if day is None:
            raise argparse.ArgumentTypeError(f'unknown day {name!r}')
        days |= day
    return days


def parse_hex_u32(text: str) -> int:
    """A 32-bit number in hex, with or without ``0x``."""

    value = hex_u32(text)
    if value is None:
        raise argparse.ArgumentTypeError(f'not a 32-bit hex number: {text!r}')
    return value


def enum_converter(enum: type[EnumT]) -> Callable[[str], EnumT]:
    """Converter accepting an enum value, case-insensitive, ``-`` for ``_``."""

    def convert(text: str) -> EnumT:
        key = text.strip().lower().replace('-', '_')
        for member in enum:
            if member.value.lower() == key:
                return member
        choices = ', '.join(member.value for member in enum)
        raise argparse.ArgumentTypeError(
            f'unknown value {text!r}; choose from {choices}')

    convert.__name__ = enum.__name__
    return convert
