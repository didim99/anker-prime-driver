"""Control commands: one write per command, with a short confirmation.

Capabilities not yet confirmed on hardware print a reminder, so a test run
doubles as verification.
"""

import argparse
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from enum import StrEnum

from ankerprime.cli.base import ChargerCommand, CliContext
from ankerprime.cli.parsing import (
    enum_converter, parse_countdown, parse_days,
    parse_output, parse_switch, parse_time)
from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability, Verification
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, DisplayLanguage, FixedAllocation, HourFormat,
    KnobDirection, ScheduleSlot, ScreenOrientation, ScreenTimeout, Weekday)
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.state import ClockSchedule, Schedule


SwitchSetter = Callable[[Charger, bool], Awaitable[None]]

SCHEDULE_SLOTS = {'on': ScheduleSlot.POWER_ON, 'off': ScheduleSlot.POWER_OFF}
SWITCHES: dict[str, tuple[Capability, SwitchSetter]] = {
    'device-identification': (
        Capability.DEVICE_IDENTIFICATION,
        lambda charger, on: charger.set_device_identification(on)),
    'max-compatibility': (
        Capability.MAX_COMPATIBILITY,
        lambda charger, on: charger.set_max_compatibility(on)),
    'ufcs': (Capability.UFCS, lambda charger, on: charger.set_ufcs(on)),
}
EXPERIMENTAL_NOTE = ('note: {capability} is not yet verified on hardware; '
                     'check the charger and report the result')


class ControlCommand(ChargerCommand):
    """Applies writes and confirms them."""

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        for capability, message in await self.apply(charger, args):
            context.print(message)
            verification = charger.state.capabilities.get(capability)
            if verification == Verification.EXPERIMENTAL:
                context.print(EXPERIMENTAL_NOTE.format(capability=capability))
        return 0

    @abstractmethod
    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        """Perform the writes; return each capability with a confirmation."""

        pass


class OutputCommand(ControlCommand):
    NAME = 'output'
    HELP = 'switch an output on or off'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_output(args.output, args.on)
        state = 'on' if args.on else 'off'
        return [(Capability.OUTPUT_SWITCH, f'{args.output} switched {state}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('output', type=parse_output,
                            help='c1-c4 or usb_a')
        parser.add_argument('on', type=parse_switch, help='on or off')


class CountdownCommand(ControlCommand):
    NAME = 'countdown'
    HELP = 'switch an output off after a delay, or cancel that'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        if args.duration is None:
            await charger.cancel_countdown(args.output)
            return [(Capability.OUTPUT_COUNTDOWN,
                     f'{args.output} countdown cancelled.')]
        await charger.start_countdown(args.output, args.duration)
        return [(Capability.OUTPUT_COUNTDOWN,
                 f'{args.output} switches off in {args.duration} s.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('output', type=parse_output,
                            help='c1-c4 or usb_a')
        parser.add_argument('duration', type=parse_countdown,
                            help='e.g. 90, 15m, 1h30m, or cancel')


class ScheduleCommand(ControlCommand):
    NAME = 'schedule'
    HELP = 'set the daily power-on or power-off time of an output'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        hour, minute = args.time
        schedule = Schedule(not args.disable, hour, minute, args.days)
        slot = SCHEDULE_SLOTS[args.slot]
        await charger.set_schedule(args.output, slot, schedule)
        state = 'disabled' if args.disable else 'enabled'
        return [(Capability.OUTPUT_SCHEDULE,
                 f'{args.output} {slot} at {hour:02d}:{minute:02d} {state}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('output', type=parse_output,
                            help='c1-c4 or usb_a')
        parser.add_argument('slot', choices=sorted(SCHEDULE_SLOTS),
                            metavar='on|off', help='power-on or power-off slot')
        parser.add_argument('time', type=parse_time, help='HH:MM')
        parser.add_argument('--days', type=parse_days,
                            default=Weekday.EVERY_DAY,
                            help='mon,tue,... or daily, weekdays, weekend, '
                                 'once (default: daily)')
        parser.add_argument('--disable', action='store_true',
                            help='store the slot switched off')


class ModeCommand(ControlCommand):
    NAME = 'mode'
    HELP = 'select the charging mode'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_charging_mode(args.mode)
        return [(Capability.CHARGING_MODE, f'Charging mode set to {args.mode}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('mode', type=enum_converter(ChargingMode),
                            help=_choices(ChargingMode))


class PriorityCommand(ControlCommand):
    NAME = 'priority'
    HELP = 'choose the preferred ports of the port-priority mode'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_preferred_outputs(args.outputs)
        names = ', '.join(args.outputs) or 'none'
        return [(Capability.PORT_PRIORITY, f'Preferred outputs: {names}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('outputs', type=parse_output, nargs='*',
                            help='up to two of c1-c4')


class AllocationCommand(ControlCommand):
    NAME = 'allocation'
    HELP = 'choose the fixed allocation of the standard mode (160W)'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_fixed_allocation(args.allocation)
        return [(Capability.FIXED_ALLOCATION,
                 f'Fixed allocation set to {args.allocation}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('allocation', type=enum_converter(FixedAllocation),
                            help=_choices(FixedAllocation))


class DisplayCommand(ControlCommand):
    """Applies every given display option in turn."""

    NAME = 'display'
    HELP = 'change display settings'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        done = []
        if args.brightness is not None:
            await charger.set_brightness(args.brightness)
            done.append((Capability.SCREEN_BRIGHTNESS,
                         f'Brightness set to {args.brightness}%.'))
        if args.screen_timeout is not None:
            await charger.set_screen_timeout(args.screen_timeout)
            done.append((Capability.SCREEN_TIMEOUT,
                         f'Screen timeout set to {args.screen_timeout}.'))
        if args.knob is not None:
            await charger.set_knob_direction(args.knob)
            done.append((Capability.KNOB_DIRECTION,
                         f'Knob direction set to {args.knob}.'))
        if args.hours is not None:
            await charger.set_hour_format(args.hours)
            done.append((Capability.HOUR_FORMAT,
                         f'Hour format set to {args.hours}.'))
        done.extend(await self._apply_rotation(charger, args))
        if not done:
            raise InvalidParameterError('give at least one display option')
        return done

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--brightness', type=int, help='percent')
        parser.add_argument('--screen-timeout',
                            type=enum_converter(ScreenTimeout),
                            help=_choices(ScreenTimeout))
        parser.add_argument('--knob', type=enum_converter(KnobDirection),
                            help=_choices(KnobDirection))
        parser.add_argument('--hours', type=enum_converter(HourFormat),
                            help=_choices(HourFormat))
        parser.add_argument('--orientation',
                            type=enum_converter(ScreenOrientation),
                            help=_choices(ScreenOrientation))
        parser.add_argument('--auto-rotation', type=parse_switch,
                            help='on or off')
        parser.add_argument('--language', type=enum_converter(DisplayLanguage),
                            help=_choices(DisplayLanguage))

    async def _apply_rotation(self, charger: Charger, args: argparse.Namespace
                              ) -> list[tuple[Capability, str]]:
        done = []
        if args.orientation is not None:
            await charger.set_screen_orientation(args.orientation)
            done.append((Capability.SCREEN_ORIENTATION,
                         f'Orientation set to {args.orientation}.'))
        if args.auto_rotation is not None:
            await charger.set_auto_rotation(args.auto_rotation)
            state = 'on' if args.auto_rotation else 'off'
            done.append((Capability.AUTO_ROTATION,
                         f'Auto-rotation switched {state}.'))
        if args.language is not None:
            await charger.set_display_language(args.language)
            done.append((Capability.DISPLAY_LANGUAGE,
                         f'Display language set to {args.language}.'))
        return done


class ClockCommand(ControlCommand):
    NAME = 'clock'
    HELP = 'switch the clock screensaver (250W)'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_clock(args.on, args.theme)
        state = 'on' if args.on else 'off'
        return [(Capability.CLOCK_SCREENSAVER, f'Clock switched {state}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('on', type=parse_switch, help='on or off')
        builtin = [theme for theme in ClockTheme if theme.value.startswith(
            'style')]
        parser.add_argument('--theme', type=enum_converter(ClockTheme),
                            help=', '.join(theme.value for theme in builtin))


class ClockScheduleCommand(ControlCommand):
    NAME = 'clock-schedule'
    HELP = 'set when the clock screensaver is shown (250W)'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        (start_hour, start_minute), (end_hour, end_minute) = args.start, args.end
        schedule = ClockSchedule(start_hour, start_minute, end_hour,
                                 end_minute, args.days)
        await charger.set_clock_schedule(schedule)
        return [(Capability.CLOCK_SCHEDULE, 'Clock schedule set.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('start', type=parse_time, help='HH:MM')
        parser.add_argument('end', type=parse_time, help='HH:MM')
        parser.add_argument('--days', type=parse_days,
                            default=Weekday.EVERY_DAY,
                            help='mon,tue,... or daily (default)')


class HolidayCommand(ControlCommand):
    NAME = 'holiday'
    HELP = 'switch holiday themes of the clock (250W)'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        await charger.set_holiday_theme(args.on)
        state = 'on' if args.on else 'off'
        return [(Capability.HOLIDAY_THEME, f'Holiday themes switched {state}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('on', type=parse_switch, help='on or off')


class SwitchCommand(ControlCommand):
    """Protocol toggles of the 250W."""

    NAME = 'switch'
    HELP = 'toggle max-compatibility, device-identification or ufcs (250W)'

    async def apply(self, charger: Charger, args: argparse.Namespace
                    ) -> list[tuple[Capability, str]]:
        capability, setter = SWITCHES[args.name]
        await setter(charger, args.on)
        state = 'on' if args.on else 'off'
        return [(capability, f'{args.name} switched {state}.')]

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('name', choices=sorted(SWITCHES))
        parser.add_argument('on', type=parse_switch, help='on or off')


def _choices(enum: type[StrEnum]) -> str:
    return ', '.join(member.value for member in enum)
