"""Every reversible write on a real 250W, each put back afterwards.

Each test reads the current value, writes another one, checks that a fresh
snapshot reports it, and restores the original in ``finally``. The last
test compares everything with the state found before the first one.
Values the charger cannot report back as they were (a schedule slot never
set) are skipped rather than changed.
"""

import io
from datetime import datetime, timedelta

import pytest

from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import CustomPortLimit
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, HourFormat, KnobDirection, OutputId,
    ScheduleSlot, Weekday)
from ankerprime.model.pictures import ClockPicture
from ankerprime.model.state import ChargerState, ClockSchedule, Schedule
from tests.hardware.helpers import (
    Fingerprint, expect, fingerprint, idle_output, need, restore_clock,
    restore_switches, settled)


pytestmark = pytest.mark.asyncio(loop_scope='session')

COUNTDOWN_SECONDS = 3600
PROFILE_NAME = 'HW test'
BUILTIN_STYLES = (ClockTheme.STYLE_1, ClockTheme.STYLE_2, ClockTheme.STYLE_3)


async def test_baseline_recorded(baseline: Fingerprint) -> None:
    """Records the starting state before any write."""

    assert baseline


# Outputs.

async def test_output_switch(charger: Charger) -> None:
    need(charger, Capability.OUTPUT_SWITCH)
    output = idle_output(charger)
    try:
        await charger.set_output(output, False)
        await expect(charger, lambda s: s.outputs[output].is_on is False,
                     f'{output.value} off')
    finally:
        await charger.set_output(output, True)
    await expect(charger, lambda s: s.outputs[output].is_on is True,
                 f'{output.value} on again')


async def test_countdown(charger: Charger) -> None:
    need(charger, Capability.OUTPUT_COUNTDOWN)
    output = idle_output(charger)
    try:
        await charger.start_countdown(output, COUNTDOWN_SECONDS)
        await expect(charger,
                     lambda s: s.outputs[output].countdown is not None,
                     f'a countdown on {output.value}')
    finally:
        await charger.cancel_countdown(output)
    await expect(charger, lambda s: s.outputs[output].countdown is None,
                 f'the countdown on {output.value} cancelled')


@pytest.mark.parametrize('slot', list(ScheduleSlot))
async def test_schedule(charger: Charger, slot: ScheduleSlot) -> None:
    need(charger, Capability.OUTPUT_SCHEDULE)
    output = idle_output(charger)

    def current(state: ChargerState) -> Schedule | None:
        control = state.outputs[output]
        return (control.power_on_schedule if slot == ScheduleSlot.POWER_ON
                else control.power_off_schedule)

    original = current(charger.state)
    if original is None:
        pytest.skip(f'{output.value} {slot.value} never set: the charger has '
                    'no "unset" value to restore')
    # Twelve hours away, on a single weekday three days ahead: it cannot
    # fire while the test runs.
    when = datetime.now() + timedelta(hours=12)
    day = Weekday(1 << ((datetime.now().weekday() + 3) % 7))
    target = Schedule(True, when.hour, when.minute, day)
    if target == original:
        target = Schedule(True, when.hour, (when.minute + 1) % 60, day)
    try:
        await charger.set_schedule(output, slot, target)
        await expect(charger, lambda s: current(s) == target,
                     f'the new {slot.value} schedule')
    finally:
        await charger.set_schedule(output, slot, original)
    await expect(charger, lambda s: current(s) == original,
                 f'the original {slot.value} schedule')


# Charging mode and priority.

async def test_charging_mode(charger: Charger) -> None:
    need(charger, Capability.CHARGING_MODE)
    original = charger.state.settings.charging_mode
    if original in (None, ChargingMode.CUSTOM):
        pytest.skip('custom mode in use; covered by the custom mode test')
    modes = [mode for mode in charger.product.charging_modes
             if mode not in (original, ChargingMode.CUSTOM)]
    target = ChargingMode.AI if ChargingMode.AI in modes else modes[0]
    try:
        await charger.set_charging_mode(target)
        await expect(charger, lambda s: s.settings.charging_mode == target,
                     f'mode {target.value}')
    finally:
        await charger.set_charging_mode(original)
    await expect(charger, lambda s: s.settings.charging_mode == original,
                 f'mode {original.value} again')


async def test_preferred_outputs(charger: Charger) -> None:
    """The write also switches to the port-priority mode: both restored."""

    need(charger, Capability.PORT_PRIORITY)
    mode = charger.state.settings.charging_mode
    if mode in (None, ChargingMode.CUSTOM):
        pytest.skip('custom mode in use; the mode could not be restored')

    def preferred(state: ChargerState) -> set[OutputId]:
        return {output for output, control in state.outputs.items()
                if control.preferred}

    original = preferred(charger.state)
    candidates = [output for output in charger.product.priority_outputs
                  if output not in original]
    target = {candidates[0]} if candidates else set()
    try:
        await charger.set_preferred_outputs(target)
        await expect(charger, lambda s: preferred(s) == target,
                     'the new preferred outputs')
    finally:
        await charger.set_preferred_outputs(original)
        state = await settled(charger)
        if state.settings.charging_mode != mode:
            await charger.set_charging_mode(mode)
    await expect(charger, lambda s: preferred(s) == original
                 and s.settings.charging_mode == mode,
                 'the original preferred outputs and mode')


# Display.

async def test_brightness(charger: Charger) -> None:
    need(charger, Capability.SCREEN_BRIGHTNESS)
    original = charger.state.settings.display.brightness
    brightness = charger.product.brightness
    assert original is not None and brightness is not None
    target = original + brightness.step
    if not brightness.contains(target):
        target = original - brightness.step
    try:
        await charger.set_brightness(target)
        await expect(charger,
                     lambda s: s.settings.display.brightness == target,
                     f'brightness {target}')
    finally:
        await charger.set_brightness(original)
    await expect(charger,
                 lambda s: s.settings.display.brightness == original,
                 f'brightness {original} again')


async def test_screen_timeout(charger: Charger) -> None:
    need(charger, Capability.SCREEN_TIMEOUT)
    original = charger.state.settings.display.screen_timeout
    assert original is not None
    target = next(timeout for timeout in charger.product.screen_timeouts
                  if timeout != original)
    try:
        await charger.set_screen_timeout(target)
        await expect(charger,
                     lambda s: s.settings.display.screen_timeout == target,
                     f'screen timeout {target.value}')
    finally:
        await charger.set_screen_timeout(original)
    await expect(charger,
                 lambda s: s.settings.display.screen_timeout == original,
                 f'screen timeout {original.value} again')


async def test_knob_direction(charger: Charger) -> None:
    need(charger, Capability.KNOB_DIRECTION)
    original = charger.state.settings.display.knob_direction
    assert original is not None
    target = next(direction for direction in KnobDirection
                  if direction != original)
    try:
        await charger.set_knob_direction(target)
        await expect(charger,
                     lambda s: s.settings.display.knob_direction == target,
                     f'knob {target.value}')
    finally:
        await charger.set_knob_direction(original)
    await expect(charger,
                 lambda s: s.settings.display.knob_direction == original,
                 f'knob {original.value} again')


async def test_hour_format(charger: Charger) -> None:
    need(charger, Capability.HOUR_FORMAT)
    original = charger.state.settings.display.hour_format
    assert original is not None
    target = next(hours for hours in HourFormat if hours != original)
    try:
        await charger.set_hour_format(target)
        await expect(charger,
                     lambda s: s.settings.display.hour_format == target,
                     f'hour format {target.value}')
    finally:
        await charger.set_hour_format(original)
    await expect(charger,
                 lambda s: s.settings.display.hour_format == original,
                 f'hour format {original.value} again')


# Clock.

async def test_clock_switch(charger: Charger) -> None:
    need(charger, Capability.CLOCK_SCREENSAVER)
    original = charger.state.settings.clock
    assert original is not None
    try:
        await charger.set_clock(not original.enabled)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.enabled != original.enabled,
                     'the clock switched')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


async def test_clock_style(charger: Charger) -> None:
    need(charger, Capability.CLOCK_SCREENSAVER)
    original = charger.state.settings.clock
    assert original is not None
    target = next(style for style in BUILTIN_STYLES
                  if style != original.theme)
    try:
        await charger.set_clock(original.enabled, target)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.theme == target,
                     f'clock {target.value}')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


async def test_clock_schedule(charger: Charger) -> None:
    need(charger, Capability.CLOCK_SCHEDULE)
    original = charger.state.settings.clock
    assert original is not None
    schedule = original.schedule
    if schedule is None:
        pytest.skip('clock schedule not reported')
    target = ClockSchedule((schedule.start_hour + 1) % 24,
                           schedule.start_minute,
                           (schedule.end_hour + 1) % 24, schedule.end_minute,
                           schedule.weekdays)
    try:
        await charger.set_clock_schedule(target)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.schedule == target,
                     'the new clock schedule')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


async def test_holiday_theme(charger: Charger) -> None:
    need(charger, Capability.HOLIDAY_THEME)
    original = charger.state.settings.clock
    assert original is not None
    if not original.enabled or original.holiday_theme is None:
        pytest.skip('the clock is off: holiday themes are not reported')
    try:
        await charger.set_holiday_theme(not original.holiday_theme)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.holiday_theme
                     != original.holiday_theme,
                     'holiday themes switched')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


async def test_catalogue_picture(charger: Charger) -> None:
    need(charger, Capability.CLOCK_PICTURE)
    original = charger.state.settings.clock
    assert original is not None
    shown = (original.picture_id if original.theme == ClockTheme.PICTURE
             else None)
    pictures = await charger.clock_pictures()
    assert pictures, 'the catalogue is empty'
    picture = next(picture for picture in pictures
                   if picture.picture_id != shown)
    try:
        await charger.apply_clock_picture(picture)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.theme == ClockTheme.PICTURE
                     and s.settings.clock.picture_id == picture.picture_id,
                     f'catalogue picture {picture.picture_id}')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


async def test_own_picture_upload_and_delete(charger: Charger) -> None:
    """Uploads to the account and deletes again; never shown on the charger."""

    need(charger, Capability.OWN_CLOCK_PICTURES)
    pytest.importorskip('PIL')
    from ankerprime.pictures import prepare_picture
    before = await charger.own_clock_pictures()
    if len(before) >= ClockPicture.OWN_LIMIT:
        pytest.skip('the account keeps the maximum of own pictures')
    known = {picture.picture_id for picture in before}
    await charger.upload_clock_picture(prepare_picture(_noise_png()))
    added = [picture for picture in await charger.own_clock_pictures()
             if picture.picture_id not in known]
    try:
        assert len(added) == 1, f'{len(added)} new own pictures'
    finally:
        for picture in added:
            await charger.delete_clock_picture(picture.picture_id)
    after = await charger.own_clock_pictures()
    assert {picture.picture_id for picture in after} == known


async def test_picture_time(charger: Charger) -> None:
    need(charger, Capability.OWN_CLOCK_PICTURES)
    original = charger.state.settings.clock
    assert original is not None
    if original.picture_time is None:
        pytest.skip('the firmware does not report the time over pictures')
    try:
        await charger.set_picture_time(not original.picture_time)
        await expect(charger,
                     lambda s: s.settings.clock is not None
                     and s.settings.clock.picture_time
                     != original.picture_time,
                     'the time over own pictures switched')
    finally:
        await restore_clock(charger, original)
    await expect(charger, lambda s: s.settings.clock == original,
                 'the original clock')


# Switches.

@pytest.mark.parametrize('capability', [Capability.MAX_COMPATIBILITY,
                                        Capability.DEVICE_IDENTIFICATION])
async def test_exclusive_switch(charger: Charger,
                                capability: Capability) -> None:
    need(charger, capability)
    settings = charger.state.settings
    compatibility = settings.max_compatibility
    identification = settings.device_identification
    if compatibility is None or identification is None:
        pytest.skip('switches not reported')
    compatible = capability == Capability.MAX_COMPATIBILITY
    target = not (compatibility if compatible else identification)
    write = (charger.set_max_compatibility if compatible
             else charger.set_device_identification)

    def value(state: ChargerState) -> bool | None:
        return (state.settings.max_compatibility if compatible
                else state.settings.device_identification)

    try:
        await write(target)
        await expect(charger, lambda s: value(s) == target,
                     f'{capability.value} {target}', account=True)
    finally:
        await restore_switches(charger, compatibility, identification)
    await expect(charger,
                 lambda s: s.settings.max_compatibility == compatibility
                 and s.settings.device_identification == identification,
                 'the original switches', account=True)


async def test_ufcs(charger: Charger) -> None:
    need(charger, Capability.UFCS)
    original = charger.state.settings.ufcs
    if original is None:
        pytest.skip('UFCS not reported')
    try:
        await charger.set_ufcs(not original)
        await expect(charger, lambda s: s.settings.ufcs is not original,
                     f'UFCS {not original}')
    finally:
        await charger.set_ufcs(original)
    await expect(charger, lambda s: s.settings.ufcs is original,
                 f'UFCS {original} again')


# Custom mode.

async def test_labs_switch(charger: Charger) -> None:
    need(charger, Capability.CUSTOM_PROFILES)
    await charger.reload_custom_profiles()
    settings = charger.state.settings
    original = settings.custom_mode_enabled
    if original is None:
        pytest.skip('the account setting is not reported')
    if original and settings.charging_mode == ChargingMode.CUSTOM:
        pytest.skip('custom mode in use: switching Labs off would leave it')
    try:
        await charger.set_custom_mode_enabled(not original)
        await expect(charger,
                     lambda s: s.settings.custom_mode_enabled is not original,
                     f'Labs {not original}', account=True)
    finally:
        await charger.set_custom_mode_enabled(original)
    await expect(charger,
                 lambda s: s.settings.custom_mode_enabled is original,
                 f'Labs {original} again', account=True)


async def test_profile_save_edit_delete(charger: Charger) -> None:
    """Stores a profile in a free slot, changes it, deletes it."""

    need(charger, Capability.CUSTOM_PROFILES)
    rules = charger.product.custom_mode
    assert rules is not None
    await charger.reload_custom_profiles()
    stored = {profile.number
              for profile in charger.state.settings.custom_profiles or ()}
    free = [number for number in rules.numbers if number not in stored]
    if not free:
        pytest.skip('every profile slot is in use')
    number = free[-1]
    profile = rules.template(number, PROFILE_NAME)

    def kept(state: ChargerState) -> int | None:
        found = state.settings.custom_profile(number)
        return found.watts(OutputId.C1) if found is not None else None

    try:
        await charger.save_custom_profile(profile)
        await expect(charger,
                     lambda s: kept(s) == profile.watts(OutputId.C1),
                     f'profile {number} stored', account=True)
        saved = charger.state.settings.custom_profile(number)
        assert saved is not None and saved.name == PROFILE_NAME
        watts = 60 if saved.watts(OutputId.C1) != 60 else 45
        port = saved.port(OutputId.C1)
        changed = saved.with_port(CustomPortLimit(
            OutputId.C1, watts, port.protocols if port is not None else ()))
        await charger.save_custom_profile(changed)
        await expect(charger, lambda s: kept(s) == watts,
                     f'profile {number} changed', account=True)
    finally:
        await charger.reload_custom_profiles()
        if charger.state.settings.custom_profile(number) is not None:
            await charger.delete_custom_profile(number)
    await expect(charger, lambda s: kept(s) is None,
                 f'profile {number} deleted', account=True)


async def test_custom_mode_apply_and_exit(charger: Charger) -> None:
    """Applies the default limits unstored, then leaves custom mode."""

    need(charger, Capability.CUSTOM_CHARGING_MODE)
    rules = charger.product.custom_mode
    assert rules is not None
    await charger.reload_custom_profiles()
    settings = charger.state.settings
    original = settings.charging_mode
    if original in (None, ChargingMode.CUSTOM):
        pytest.skip('custom mode already in use')
    if settings.custom_mode_enabled is False:
        pytest.skip('the account\'s Labs setting is off')
    profile = rules.template(rules.numbers[0], 'Custom')
    try:
        await charger.apply_custom_profile(profile)
        await expect(charger,
                     lambda s: s.settings.charging_mode == ChargingMode.CUSTOM,
                     'custom mode')
    finally:
        if charger.state.settings.charging_mode == ChargingMode.CUSTOM:
            await charger.exit_custom_mode()
        state = await settled(charger)
        if state.settings.charging_mode != original:
            await charger.set_charging_mode(original)
    await expect(charger, lambda s: s.settings.charging_mode == original,
                 f'mode {original.value} again')


# Everything back.

async def test_state_restored(charger: Charger, baseline: Fingerprint) -> None:
    """Runs last: every value the tests touched is as found."""

    now = fingerprint(await settled(charger, account=True))
    changed = {key: (baseline[key], value) for key, value in now.items()
               if key in baseline and baseline[key] != value}
    assert not changed, f'not restored (before, now): {changed}'


def _noise_png() -> bytes:
    """A detailed picture, so the JPEG reaches the official size window."""

    from PIL import Image
    noise = Image.merge('RGB', [Image.effect_noise((640, 480), 60)
                                for _ in range(3)])
    buffer = io.BytesIO()
    noise.save(buffer, format='PNG')
    return buffer.getvalue()
