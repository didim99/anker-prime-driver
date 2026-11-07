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
    CUSTOM_PROFILES = 'custom_profiles'
    """Custom profiles and the custom mode switch kept in the account."""
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
    """Picture themes of the clock from the Anker cloud catalogue."""

    OWN_CLOCK_PICTURES = 'own_clock_pictures'
    """The account's own clock pictures and the time shown over them."""
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
