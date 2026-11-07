"""Reading back what the charger reports, and putting settings back."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import ClockTheme, OutputId
from ankerprime.model.pictures import ClockPicture
from ankerprime.model.state import ChargerState, ClockSettings


# Seconds for a requested snapshot to arrive over the cloud; set from
# ``--hardware-settle`` by the ``charger`` fixture.
SETTLE = 4.0
PICTURE_THEMES = (ClockTheme.PICTURE, ClockTheme.CUSTOM)

Fingerprint = dict[str, Any]


def need(charger: Charger, *capabilities: Capability) -> None:
    """Skip unless the charger supports every capability."""

    for capability in capabilities:
        if not charger.supports(capability):
            pytest.skip(f'{capability.value} not supported')


async def settled(charger: Charger, *, account: bool = False
                  ) -> ChargerState:
    """The state after a fresh snapshot: what the charger itself reports.

    A write already updates the state once acknowledged; the snapshot shows
    whether the charger kept it. ``account`` also re-reads the account's
    settings and profiles.
    """

    await charger.refresh()
    await asyncio.sleep(SETTLE)
    if account and charger.supports(Capability.CUSTOM_PROFILES):
        await charger.reload_custom_profiles()
    return charger.state


async def expect(charger: Charger, check: Callable[[ChargerState], bool],
                 what: str, *, account: bool = False) -> None:
    """Assert ``check`` on the read-back state."""

    state = await settled(charger, account=account)
    assert check(state), f'charger does not report {what}'


def fingerprint(state: ChargerState) -> Fingerprint:
    """Every value the tests change, for comparing before and after."""

    settings = state.settings
    display = settings.display
    clock = settings.clock
    values: Fingerprint = {
        'charging_mode': settings.charging_mode,
        'brightness': display.brightness,
        'screen_timeout': display.screen_timeout,
        'knob_direction': display.knob_direction,
        'hour_format': display.hour_format,
        'max_compatibility': settings.max_compatibility,
        'device_identification': settings.device_identification,
        'ufcs': settings.ufcs,
        'custom_mode_enabled': settings.custom_mode_enabled,
        'custom_profiles': settings.custom_profiles,
    }
    if clock is not None:
        values.update({
            'clock_enabled': clock.enabled, 'clock_theme': clock.theme,
            'clock_picture': clock.picture_id,
            'holiday_theme': clock.holiday_theme,
            'clock_schedule': clock.schedule,
            'picture_time': clock.picture_time})
    for output, control in state.outputs.items():
        values[f'{output.value}.on'] = control.is_on
        values[f'{output.value}.countdown'] = control.countdown is not None
        values[f'{output.value}.power_on'] = control.power_on_schedule
        values[f'{output.value}.power_off'] = control.power_off_schedule
        # Reported only in some modes; unknown counts as not preferred.
        values[f'{output.value}.preferred'] = bool(control.preferred)
    return values


def idle_output(charger: Charger) -> OutputId:
    """An output that is on, without a countdown and powering nothing.

    Skips when every output is busy, so no plugged-in device is cut off.
    """

    state = charger.state
    for spec in reversed(charger.product.outputs):
        control = state.outputs.get(spec.output)
        if control is None or not control.is_on or control.countdown:
            continue
        readings = [state.ports.get(port) for port in spec.ports]
        if all(reading is not None and not reading.delivering
               and reading.power < 0.5 for reading in readings):
            return spec.output
    pytest.skip('no idle output: every output is off or powers a device')


async def find_picture(charger: Charger, theme: ClockTheme | None,
                       picture_id: int) -> ClockPicture:
    """The catalogue or own picture the clock showed."""

    if theme == ClockTheme.CUSTOM:
        pictures = await charger.own_clock_pictures()
    else:
        pictures = await charger.clock_pictures()
    for picture in pictures:
        if picture.picture_id == picture_id:
            return picture
    raise AssertionError(f'clock picture {picture_id} not found to restore')


async def restore_clock(charger: Charger, original: ClockSettings) -> None:
    """Put back the clock as found: theme or picture, switch, extras."""

    clock = charger.state.settings.clock
    assert clock is not None
    if original.theme in PICTURE_THEMES and original.picture_id is not None:
        if (clock.theme != original.theme
                or clock.picture_id != original.picture_id):
            picture = await find_picture(charger, original.theme,
                                         original.picture_id)
            await charger.apply_clock_picture(picture)
        if not original.enabled:
            await charger.set_clock(False)
    elif clock.enabled != original.enabled or clock.theme != original.theme:
        await charger.set_clock(original.enabled, original.theme)
    clock = charger.state.settings.clock
    assert clock is not None
    if (original.holiday_theme is not None
            and clock.holiday_theme != original.holiday_theme):
        await charger.set_holiday_theme(original.holiday_theme)
    if original.schedule is not None and clock.schedule != original.schedule:
        await charger.set_clock_schedule(original.schedule)
    if (original.picture_time is not None
            and clock.picture_time != original.picture_time):
        await charger.set_picture_time(original.picture_time)


async def restore_switches(charger: Charger, max_compatibility: bool,
                           device_identification: bool) -> None:
    """Put back the two exclusive switches.

    Switching one on switches the other off, so the one that was on goes
    last; with both off, each is switched off.
    """

    settings = charger.state.settings
    if max_compatibility:
        await charger.set_max_compatibility(True)
    elif device_identification:
        await charger.set_device_identification(True)
    else:
        if settings.max_compatibility:
            await charger.set_max_compatibility(False)
        if settings.device_identification:
            await charger.set_device_identification(False)
