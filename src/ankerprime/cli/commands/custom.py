"""Custom charging mode: stored profiles and the Labs switch (250W)."""

import argparse

from ankerprime.cli.base import ChargerCommand, CliContext
from ankerprime.cli.commands.control import confirm
from ankerprime.cli.parsing import enum_converter, parse_output, parse_switch
from ankerprime.cli.render import render_table
from ankerprime.cli.style import Painter
from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import (
    ChargingProfile, CustomModeRules, CustomPortLimit, CustomProtocol)
from ankerprime.model.enums import OutputId
from ankerprime.model.errors import (
    InvalidParameterError, UnsupportedCapabilityError)


PORT_OPTIONS = {
    OutputId.C1: 'c1',
    OutputId.C2: 'c2',
    OutputId.C3: 'c3',
    OutputId.C4: 'c4',
    OutputId.USB_A: 'usb_a',
}
PROTOCOL_LABELS = {
    CustomProtocol.SCP: 'SCP',
    CustomProtocol.HUAWEI: 'Huawei SCP',
    CustomProtocol.UFCS: 'UFCS',
    CustomProtocol.PD_12V: 'PD 12V',
    CustomProtocol.PPS_11V: 'PPS 11V',
    CustomProtocol.PPS_16V: 'PPS 16V',
    CustomProtocol.PPS_21V: 'PPS 21V',
    CustomProtocol.XIAOMI: 'Xiaomi',
}

parse_protocol = enum_converter(CustomProtocol)


def parse_port_protocols(text: str
                         ) -> tuple[OutputId, frozenset[CustomProtocol]]:
    """``c1=scp,ufcs,pps_11v``; an empty list (``c1=``) allows none."""

    output_text, separator, names = text.partition('=')
    if not separator:
        raise argparse.ArgumentTypeError(
            f'expected PORT=PROTOCOL,..., got {text!r}')
    output = parse_output(output_text)
    protocols = frozenset(parse_protocol(name)
                          for name in names.split(',') if name.strip())
    return output, protocols


class CustomModeCommand(ChargerCommand):
    """Lists, edits and applies custom profiles.

    Profiles live in the Anker account; ``apply`` writes one to the charger.
    """

    NAME = 'custom-mode'
    HELP = 'manage and apply custom charging profiles (250W)'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        action = args.action or 'list'
        if action == 'exit':
            await charger.exit_custom_mode()
            confirm(context, charger, Capability.CUSTOM_CHARGING_MODE,
                          'Custom mode left.')
            return 0
        await charger.reload_custom_profiles()
        match action:
            case 'list':
                context.print(render_profiles(charger, context.painter))
            case 'show':
                context.print(render_profile(_stored(charger, args.number),
                                             charger, context.painter))
            case 'add' | 'edit':
                await self._save(context, charger, args)
            case 'delete':
                _stored(charger, args.number)
                await charger.delete_custom_profile(args.number)
                confirm(context, charger, Capability.CUSTOM_PROFILES,
                              f'Profile {args.number} deleted.')
            case 'apply':
                profile = _stored(charger, args.number)
                await charger.apply_custom_profile(profile)
                confirm(context, charger,
                              Capability.CUSTOM_CHARGING_MODE,
                              f'Profile {profile.number} "{profile.name}" '
                              f'applied.')
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest='action', metavar='action')
        actions.add_parser('list', help='show the stored profiles')
        show = actions.add_parser('show', help='show one profile in detail')
        show.add_argument('number', type=int)
        add = actions.add_parser(
            'add', help='store a new profile, starting from the defaults')
        add.add_argument('number', type=int, help='1-4')
        add.add_argument('name', help='up to 20 characters')
        _profile_options(add)
        edit = actions.add_parser('edit', help='change a stored profile')
        edit.add_argument('number', type=int)
        edit.add_argument('--name', help='up to 20 characters')
        _profile_options(edit)
        delete = actions.add_parser('delete', help='remove a stored profile')
        delete.add_argument('number', type=int)
        apply = actions.add_parser('apply',
                                   help='switch to custom mode with a profile')
        apply.add_argument('number', type=int)
        actions.add_parser('exit', help='leave custom mode')

    async def _save(self, context: CliContext, charger: Charger,
                    args: argparse.Namespace) -> None:
        rules = _rules(charger)
        stored = charger.state.settings.custom_profile(args.number)
        if args.action == 'add':
            if stored is not None:
                raise InvalidParameterError(
                    f'profile {args.number} exists; change it with "edit"')
            profile = rules.template(args.number, args.name)
        else:
            if stored is None:
                raise InvalidParameterError(f'no profile {args.number}')
            profile = stored
            if args.name is not None:
                profile = profile.replace(name=args.name)
        profile = apply_options(profile, args)
        await charger.save_custom_profile(profile)
        verb = 'stored' if args.action == 'add' else 'changed'
        message = f'Profile {args.number} {verb}.'
        if charger.state.settings.active_profile == args.number:
            message += ' It is in use; the charger has the new limits.'
        confirm(context, charger, Capability.CUSTOM_PROFILES, message)



class LabsCommand(ChargerCommand):
    """The account's switch that makes custom mode available."""

    NAME = 'labs'
    HELP = 'switch the custom mode setting of the Anker account (250W)'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        if args.on is None:
            await charger.reload_custom_profiles()
            enabled = charger.state.settings.custom_mode_enabled
            state = 'unknown' if enabled is None else (
                'on' if enabled else 'off')
            context.print(f'Custom mode setting: {state}')
            return 0
        await charger.set_custom_mode_enabled(args.on)
        state = 'on' if args.on else 'off'
        confirm(context, charger, Capability.CUSTOM_PROFILES,
                f'Custom mode setting switched {state}.')
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('on', type=parse_switch, nargs='?',
                            help='on or off; without it, show the setting')


def apply_options(profile: ChargingProfile, args: argparse.Namespace
                  ) -> ChargingProfile:
    """Apply the watt, protocol and auto-exit options to a profile."""

    for output, option in PORT_OPTIONS.items():
        watts = getattr(args, option)
        if watts is not None:
            current = profile.port(output)
            protocols = current.protocols if current is not None else ()
            profile = profile.with_port(CustomPortLimit(output, watts,
                                                        protocols))
    for output, protocols in args.protocols or ():
        current = profile.port(output)
        watts = current.watts if current is not None else 0
        profile = profile.with_port(CustomPortLimit(output, watts, protocols))
    if args.auto_exit is not None:
        profile = profile.replace(auto_exit=args.auto_exit)
    return profile


def render_profiles(charger: Charger, painter: Painter) -> str:
    settings = charger.state.settings
    enabled = settings.custom_mode_enabled
    lines = []
    if enabled is False:
        lines.append(painter.warn('The custom mode setting is off; switch '
                                  'it on with "ankerprime labs on".'))
    profiles = settings.custom_profiles or ()
    if not profiles:
        lines.append('No stored profiles; add one with "ankerprime '
                     'custom-mode add NUMBER NAME".')
        return '\n'.join(lines)
    rules = _rules(charger)
    header = ('', 'No', 'Name', *(_port_label(rule.output)
                                  for rule in rules.ports),
              'Total', 'Auto-exit')
    rows = []
    for profile in profiles:
        active = settings.active_profile == profile.number
        rows.append((painter.good('*') if active else '',
                     str(profile.number), painter.accent(profile.name),
                     *(f'{profile.watts(rule.output)} W'
                       for rule in rules.ports),
                     f'{profile.total_watts} W',
                     'on' if profile.auto_exit else painter.dim('off')))
    lines.append(render_table(header, rows, painter=painter))
    return '\n'.join(lines)


def render_profile(profile: ChargingProfile, charger: Charger,
                   painter: Painter) -> str:
    rules = _rules(charger)
    settings = charger.state.settings
    title = f'Profile {profile.number}: {profile.name}'
    if settings.active_profile == profile.number:
        title += ' (in use)'
    lines = [painter.strong(title),
             f'Total {profile.total_watts} W of {rules.total_limit} W, '
             f'auto-exit {"on" if profile.auto_exit else "off"}']
    rows = []
    for rule in rules.ports:
        limit = profile.port(rule.output)
        protocols = _protocols(limit, profile) if limit is not None else ''
        watts = limit.watts if limit is not None else 0
        rows.append((_port_label(rule.output), f'{watts} W',
                     protocols or painter.dim('none')))
    lines.append(render_table(('Port', 'Limit', 'Protocols'), rows,
                              painter=painter))
    if not profile.protocols_enabled:
        lines.append(painter.dim('Saved without protocol choices.'))
    return '\n'.join(lines)


def _protocols(limit: CustomPortLimit, profile: ChargingProfile) -> str:
    if not profile.protocols_enabled:
        return ''
    return ', '.join(PROTOCOL_LABELS[protocol] for protocol in CustomProtocol
                     if protocol in limit.protocols)


def _profile_options(parser: argparse.ArgumentParser) -> None:
    for output, option in PORT_OPTIONS.items():
        parser.add_argument(f'--{option.replace("_", "-")}', dest=option,
                            type=int, metavar='W',
                            help=f'limit of {_port_label(output)} in watts '
                                 '(0 switches it off)')
    choices = ', '.join(protocol.value for protocol in CustomProtocol)
    parser.add_argument('--protocols', type=parse_port_protocols,
                        action='append', metavar='PORT=LIST',
                        help='protocols a port allows, replacing its list, '
                             f'e.g. c1=ufcs,pps_11v; choose from {choices}')
    parser.add_argument('--auto-exit', type=parse_switch,
                        help='on or off: leave custom mode once every '
                             'device is unplugged')


def _stored(charger: Charger, number: int) -> ChargingProfile:
    profile = charger.state.settings.custom_profile(number)
    if profile is None:
        raise InvalidParameterError(f'no profile {number}')
    return profile


def _rules(charger: Charger) -> CustomModeRules:
    rules = charger.product.custom_mode
    if rules is None:
        raise UnsupportedCapabilityError(Capability.CUSTOM_CHARGING_MODE)
    return rules


def _port_label(output: OutputId) -> str:
    return 'USB-A' if output == OutputId.USB_A else output.value.upper()
