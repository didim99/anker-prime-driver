"""Energy counter commands."""

import argparse

from ankerprime.cli.base import Command, CliContext, select_device
from ankerprime.cli.parsing import parse_port
from ankerprime.energy.store import EnergyStore
from ankerprime.model.enums import PortId
from ankerprime.utils import utc_now


class EnergyResetCommand(Command):
    """Zeroes stored counters; a running ``monitor`` follows the reset.

    With ``--serial`` it works offline; otherwise the account picks the only
    cloud charger.
    """

    NAME = 'energy-reset'
    HELP = 'set the stored energy counters to zero'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        serial = args.serial
        if serial is None:
            async with context.account() as account:
                devices = await account.bound_devices()
            serial = select_device(devices, None).serial
        store = EnergyStore.for_serial(context.config.energy_store(True),
                                       serial)
        record = await store.reset(args.ports or None, utc_now())
        if not record.counters:
            context.print('No energy counters are stored for this charger.')
            return 0
        order = list(PortId)
        reset = sorted(args.ports or record.counters, key=order.index)
        names = ', '.join(port.value for port in reset)
        context.print(f'Energy counters reset: {names}.')
        context.print('A running monitor follows within a minute.')
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('ports', nargs='*', type=parse_port,
                            metavar='port',
                            help='c1-c4, a1, a2 (default: all)')
        parser.add_argument('--serial',
                            help='charger serial; needed with several '
                                 'chargers, works offline')
