"""Charger aliases: charger list, add, remove, default."""

import argparse

from ankerprime.cli.aliases import (
    ChargerAlias, ChargerAliases, Connection, parse_alias_name,
    parse_country)
from ankerprime.cli.base import CliContext, Command
from ankerprime.cli.render import render_table


class ChargerAliasCommand(Command):
    """Names chargers so commands take ``-c NAME`` or nothing at all.

    ``list`` is the default action. The default charger is used by every
    charger command given no ``-c``, ``--serial`` or ``--ble``.
    """

    NAME = 'charger'
    HELP = 'name chargers and choose the default one'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        aliases = await context.config.aliases()
        match args.action:
            case 'add':
                self._add(context, aliases, args)
            case 'remove':
                alias = aliases.remove(args.name)
                context.print(context.painter.good(
                    f'Charger {alias.name} removed.'))
            case 'default':
                if not self._default(context, aliases, args):
                    return 0
            case _:
                context.print(render_aliases(context, aliases))
                return 0
        await context.config.save_aliases(aliases)
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest='action', metavar='action')
        actions.add_parser('list', help='show the saved chargers')
        add = actions.add_parser('add', help='save or replace a charger')
        add.add_argument('name', type=parse_alias_name)
        target = add.add_mutually_exclusive_group(required=True)
        target.add_argument('--serial', help='serial of a cloud charger')
        target.add_argument('--ble', metavar='ADDRESS',
                            help='Bluetooth address of a 160W charger')
        add.add_argument('--owner-id',
                         help='Anker account ID the 160W is bound to')
        add.add_argument('--country', type=parse_country,
                         help='account country code for the 160W')
        add.add_argument('--default', action='store_true',
                         help='also make it the default charger')
        remove = actions.add_parser('remove', help='forget a charger')
        remove.add_argument('name', type=parse_alias_name)
        default = actions.add_parser(
            'default', help='show or choose the default charger')
        choice = default.add_mutually_exclusive_group()
        choice.add_argument('name', type=parse_alias_name, nargs='?')
        choice.add_argument('--clear', action='store_true',
                            help='use no default charger')

    def _add(self, context: CliContext, aliases: ChargerAliases,
             args: argparse.Namespace) -> None:
        if args.ble is not None:
            alias = ChargerAlias(args.name, Connection.BLUETOOTH,
                                 address=args.ble.upper(),
                                 owner_id=args.owner_id,
                                 country=args.country)
        else:
            if args.owner_id is not None or args.country is not None:
                context.print(context.painter.warn(
                    '--owner-id and --country apply to Bluetooth chargers '
                    'only; ignored.'))
            alias = ChargerAlias(args.name, Connection.CLOUD,
                                 serial=args.serial.upper())
        replaced = aliases.put(alias)
        verb = 'replaced' if replaced else 'saved'
        context.print(context.painter.good(f'Charger {alias.name} {verb}.'))
        if args.default:
            aliases.set_default(alias.name)
            context.print(f'{alias.name} is now the default charger.')

    def _default(self, context: CliContext, aliases: ChargerAliases,
                 args: argparse.Namespace) -> bool:
        """Apply or show the default; return whether to save."""

        if args.clear:
            aliases.set_default(None)
            context.print('No default charger.')
            return True
        if args.name is None:
            current = aliases.default
            context.print(current.name if current is not None
                          else 'No default charger.')
            return False
        aliases.set_default(args.name)
        context.print(context.painter.good(
            f'{args.name} is now the default charger.'))
        return True


def render_aliases(context: CliContext, aliases: ChargerAliases) -> str:
    painter = context.painter
    if not aliases.aliases:
        return ('No saved chargers; add one with "ankerprime charger add '
                'NAME --serial SERIAL" or "--ble ADDRESS".')
    default = aliases.default
    rows = []
    for alias in aliases.aliases:
        details = [f'{label} {value}' for label, value in (
            ('serial', alias.serial
             if alias.connection == Connection.BLUETOOTH else None),
            ('owner', alias.owner_id), ('country', alias.country))
            if value is not None]
        marker = painter.good('*') if alias is default else ''
        rows.append((marker, painter.accent(alias.name),
                     alias.connection.value, alias.target,
                     painter.dim(', '.join(details))))
    return render_table(('', 'Name', 'Connection', 'Charger', 'Details'),
                        rows, painter=painter)
