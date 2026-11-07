"""Wire codes of the A2345 for the shared model enums."""

from ankerprime.model.enums import (
    CableRating, ChargingMode, ChargingProtocol, ClockTheme, DeviceBrand,
    HourFormat, KnobDirection, OutputId, ScheduleSlot, ScreenTimeout)
from ankerprime.model.errors import InvalidParameterError


# Wire order of the port groups: the index is the group code.
GROUPS = (OutputId.C1, OutputId.C2, OutputId.C3, OutputId.C4, OutputId.USB_A)
USB_C_GROUPS = GROUPS[:4]

CHARGING_MODE_CODES = {
    ChargingMode.AI: 1,
    ChargingMode.CUSTOM: 5,
    ChargingMode.DUAL_LAPTOP: 3,
    ChargingMode.LOW_CURRENT: 4,
    ChargingMode.PORT_PRIORITY: 2,
}
CLOCK_THEME_CODES = {
    ClockTheme.CUSTOM: 5,
    ClockTheme.PICTURE: 3,
    ClockTheme.STYLE_1: 0,
    ClockTheme.STYLE_2: 1,
    ClockTheme.STYLE_3: 2,
}
HOUR_FORMAT_CODES = {HourFormat.TWELVE_HOUR: 0, HourFormat.TWENTY_FOUR_HOUR: 1}
KNOB_DIRECTION_CODES = {KnobDirection.BACKWARD: 1, KnobDirection.FORWARD: 0}
SCHEDULE_SLOT_CODES = {ScheduleSlot.POWER_OFF: 0, ScheduleSlot.POWER_ON: 1}
SCREEN_TIMEOUT_CODES = {
    ScreenTimeout.ALWAYS_ON: 0,
    ScreenTimeout.FIVE_MINUTES: 3,
    ScreenTimeout.ONE_MINUTE: 2,
    ScreenTimeout.THIRTY_MINUTES: 4,
    ScreenTimeout.THIRTY_SECONDS: 1,
}

CABLE_RATINGS = {
    0: CableRating.THREE_AMP,
    1: CableRating.FIVE_AMP,
    2: CableRating.EPR,
    3: CableRating.HUAWEI,
    4: CableRating.XIAOMI,
}
CHARGING_PROTOCOLS = {
    1: ChargingProtocol.HUAWEI_SCP,
    2: ChargingProtocol.XIAOMI_HYPERCHARGE,
}
DEVICE_BRANDS = {1: DeviceBrand.HUAWEI_HONOR, 2: DeviceBrand.XIAOMI}


def group_code(output: OutputId) -> int:
    """Return the wire group of an output; raises for outputs the A2345 lacks."""

    if output not in GROUPS:
        raise InvalidParameterError(f'A2345 has no output {output}')
    return GROUPS.index(output)


def output_of_group(code: int | None) -> OutputId | None:
    """Return the output of a wire group, None for unknown groups."""

    if code is None or not 0 <= code < len(GROUPS):
        return None
    return GROUPS[code]
