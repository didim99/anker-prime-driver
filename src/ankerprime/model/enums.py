"""Enumerations shared by product definitions and charger state.

String values are stable identifiers suitable for persistence and translation keys.
"""

from enum import IntFlag, StrEnum


class ProductCode(StrEnum):
    """Anker model code of a supported charger."""

    A2345 = 'A2345'
    A2687 = 'A2687'


class PortId(StrEnum):
    """Physical charging port."""

    C1 = 'c1'
    C2 = 'c2'
    C3 = 'c3'
    C4 = 'c4'
    A1 = 'a1'
    A2 = 'a2'


class OutputId(StrEnum):
    """Independently switchable output: one port or a group sharing one switch."""

    C1 = 'c1'
    C2 = 'c2'
    C3 = 'c3'
    C4 = 'c4'
    USB_A = 'usb_a'


class ConnectorType(StrEnum):
    """Port connector type."""

    USB_C = 'usb_c'
    USB_A = 'usb_a'


class ChargingMode(StrEnum):
    """Power allocation mode; each product supports a subset."""

    AI = 'ai'
    STANDARD = 'standard'
    PORT_PRIORITY = 'port_priority'
    DUAL_LAPTOP = 'dual_laptop'
    LOW_CURRENT = 'low_current'
    CUSTOM = 'custom'


class FixedAllocation(StrEnum):
    """Allocation variant offered under the standard charging mode (160W)."""

    C1_PRIORITY = 'c1_priority'
    DUAL_LAPTOP = 'dual_laptop'


class ScreenTimeout(StrEnum):
    """Display auto-off delay; each product supports a subset."""

    ALWAYS_ON = 'always_on'
    THIRTY_SECONDS = '30s'
    ONE_MINUTE = '1min'
    FIVE_MINUTES = '5min'
    THIRTY_MINUTES = '30min'
    TWELVE_HOURS = '12h'


class DisplayLanguage(StrEnum):
    """Display language (160W)."""

    ENGLISH = 'en'
    CHINESE_SIMPLIFIED = 'zh_hans'
    JAPANESE = 'ja'
    GERMAN = 'de'


class ScreenOrientation(StrEnum):
    """Manual display orientation (160W)."""

    UP = 'up'
    LEFT = 'left'
    DOWN = 'down'
    RIGHT = 'right'


class KnobDirection(StrEnum):
    """Knob rotation direction (250W)."""

    FORWARD = 'forward'
    BACKWARD = 'backward'


class HourFormat(StrEnum):
    """Clock hour format (250W)."""

    TWELVE_HOUR = '12h'
    TWENTY_FOUR_HOUR = '24h'


class ClockTheme(StrEnum):
    """Clock screensaver theme (250W): built-in styles or pictures."""

    STYLE_1 = 'style_1'
    STYLE_2 = 'style_2'
    STYLE_3 = 'style_3'
    PICTURE = 'picture'
    CUSTOM = 'custom'


class ScheduleSlot(StrEnum):
    """One of the two daily schedule slots of an output (250W)."""

    POWER_ON = 'power_on'
    POWER_OFF = 'power_off'


class Weekday(IntFlag):
    """Weekday mask of a schedule; an empty mask means "once"."""

    ONCE = 0
    MONDAY = 1 << 0
    TUESDAY = 1 << 1
    WEDNESDAY = 1 << 2
    THURSDAY = 1 << 3
    FRIDAY = 1 << 4
    SATURDAY = 1 << 5
    SUNDAY = 1 << 6
    EVERY_DAY = 0x7F


class CableRating(StrEnum):
    """Cable capability detected on a USB-C port."""

    THREE_AMP = '3a'
    FIVE_AMP = '5a'
    EPR = 'epr'
    HUAWEI = 'huawei'
    XIAOMI = 'xiaomi'


class ChargingProtocol(StrEnum):
    """Vendor fast-charge protocol negotiated on a port."""

    APPLE_PD = 'apple_pd'
    SAMSUNG_FAST = 'samsung_fast'
    SAMSUNG_SUPER_FAST = 'samsung_super_fast'
    HUAWEI_SCP = 'huawei_scp'
    XIAOMI_HYPERCHARGE = 'xiaomi_hypercharge'


class DeviceBrand(StrEnum):
    """Phone brand recognised on a port (mainland 250W firmware only)."""

    HUAWEI_HONOR = 'huawei_honor'
    XIAOMI = 'xiaomi'
