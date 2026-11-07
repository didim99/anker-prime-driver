"""Energy counter commands."""

import argparse

from ankerprime.cli.aliases import parse_alias_name
from ankerprime.cli.base import Command, CliContext, select_device
from ankerprime.cli.parsing import parse_port
from ankerprime.energy.store import EnergyStore
from ankerprime.model.enums import PortId
from ankerprime.model.errors import InvalidParameterError
from ankerprime.utils import utc_now


class EnergyResetCommand(Command):
    """Zeroes stored counters; a running ``monitor`` follows the reset.

    With ``--serial`` or a saved charger (``-c`` or the default one) it works
    offline; otherwise the account picks the only cloud charger. A saved
    Bluetooth charger needs one connection first, which records its serial.
    """

    NAME = 'energy-reset'
    HELP = 'set the stored energy counters to zero'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        serial = args.serial
        if serial is None:
            serial = await _alias_serial(context, args.charger)
        if serial is None:
            async with context.account() as account:
                devices = await account.bound_devices()
            serial = select_device(devices, None).serial
        store = EnergyStore.for_serial(context.config.energy_store(True),
                                       serial)
        record = await store.reset(args.ports or None, utc_now())
        painter = context.painter
        if not record.counters:
            context.print(painter.warn('No energy counters are stored for '
                                       'this charger.'))
            return 0
        order = list(PortId)
        reset = sorted(args.ports or record.counters, key=order.index)
        names = ', '.join(port.value for port in reset)
        context.print(painter.good(f'Energy counters reset: {names}.'))
        context.print(painter.dim('A running monitor follows within a '
                                  'minute.'))
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('ports', nargs='*', type=parse_port,
                            metavar='port',
                            help='c1-c4, a1, a2 (default: all)')
        selection = parser.add_mutually_exclusive_group()
        selection.add_argument('-c', '--charger', type=parse_alias_name,
                               metavar='NAME',
                               help='charger saved with "charger add" '
                                    '(default: the default charger)')
        selection.add_argument('--serial',
                               help='charger serial; needed with several '
                                    'chargers, works offline')


async def _alias_serial(context: CliContext, name: str | None) -> str | None:
    """Serial of the named or default charger; None without either."""

    aliases = await context.config.aliases()
    alias = aliases.get(name) if name is not None else aliases.default
    if alias is None:
        return None
    if alias.serial is None:
        raise InvalidParameterError(
            f'serial of charger {alias.name} not known yet; connect to it '
            f'once (e.g. "ankerprime status -c {alias.name}")')
    return alias.serial
