"""CLI sub-commands, registered by name."""

from ankerprime.cli.base import Command
from ankerprime.cli.commands.account import (
    DevicesCommand, LoginCommand, LogoutCommand,
    PhoneLoginCommand, RegionsCommand)
from ankerprime.cli.commands.bluetooth import ScanCommand
from ankerprime.cli.commands.chargers import ChargerAliasCommand
from ankerprime.cli.commands.control import (
    AllocationCommand, ClockCommand, ClockScheduleCommand,
    CountdownCommand, DisplayCommand, HolidayCommand, ModeCommand,
    OutputCommand, PriorityCommand, ScheduleCommand, SwitchCommand)
from ankerprime.cli.commands.custom import CustomModeCommand, LabsCommand
from ankerprime.cli.commands.energy import EnergyResetCommand
from ankerprime.cli.commands.pictures import (
    ClockPictureCommand, ScreenPictureCommand)
from ankerprime.cli.commands.reading import MonitorCommand, StatusCommand


COMMANDS: dict[str, type[Command]] = {
    'allocation': AllocationCommand,
    'charger': ChargerAliasCommand,
    'clock': ClockCommand,
    'clock-picture': ClockPictureCommand,
    'clock-schedule': ClockScheduleCommand,
    'countdown': CountdownCommand,
    'custom-mode': CustomModeCommand,
    'devices': DevicesCommand,
    'display': DisplayCommand,
    'energy-reset': EnergyResetCommand,
    'holiday': HolidayCommand,
    'labs': LabsCommand,
    'login': LoginCommand,
    'login-phone': PhoneLoginCommand,
    'logout': LogoutCommand,
    'mode': ModeCommand,
    'monitor': MonitorCommand,
    'output': OutputCommand,
    'priority': PriorityCommand,
    'regions': RegionsCommand,
    'scan': ScanCommand,
    'schedule': ScheduleCommand,
    'screen-picture': ScreenPictureCommand,
    'status': StatusCommand,
    'switch': SwitchCommand,
}


__all__ = ['COMMANDS']
