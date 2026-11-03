"""Controllable features and how far each implementation is trusted."""

from enum import StrEnum


class Capability(StrEnum):
    """A controllable feature; telemetry and energy are always available."""

    OUTPUT_SWITCH = 'output_switch'
    OUTPUT_COUNTDOWN = 'output_countdown'
    OUTPUT_SCHEDULE = 'output_schedule'
    CHARGING_MODE = 'charging_mode'
    PORT_PRIORITY = 'port_priority'
    FIXED_ALLOCATION = 'fixed_allocation'
    CUSTOM_CHARGING_MODE = 'custom_charging_mode'
    SCREEN_BRIGHTNESS = 'screen_brightness'
    SCREEN_TIMEOUT = 'screen_timeout'
    SCREEN_ORIENTATION = 'screen_orientation'
    AUTO_ROTATION = 'auto_rotation'
    DISPLAY_LANGUAGE = 'display_language'
    KNOB_DIRECTION = 'knob_direction'
    HOUR_FORMAT = 'hour_format'
    CLOCK_SCREENSAVER = 'clock_screensaver'
    CLOCK_SCHEDULE = 'clock_schedule'
    HOLIDAY_THEME = 'holiday_theme'
    CLOCK_PICTURE = 'clock_picture'
    SCREEN_PICTURE = 'screen_picture'
    MAX_COMPATIBILITY = 'max_compatibility'
    DEVICE_IDENTIFICATION = 'device_identification'
    UFCS = 'ufcs'


class Verification(StrEnum):
    """Trust level of a capability implementation."""

    VERIFIED = 'verified'
    """Confirmed on real hardware."""

    EXPERIMENTAL = 'experimental'
    """Implemented from the reference app, not confirmed on hardware."""

    PLANNED = 'planned'
    """Known to exist on the charger, not implemented yet."""
