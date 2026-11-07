"""Bluetooth commands: scan."""

import argparse

from ankerprime.cli.base import CliContext, Command
from ankerprime.cli.render import render_table


DEFAULT_SCAN_TIMEOUT = 10.0


class ScanCommand(Command):
    """Lists chargers advertising nearby, model-confirmed ones first.

    "possible" marks devices that only advertise the service shared by the
    Anker family (power stations, other chargers).
    """

    NAME = 'scan'
    HELP = 'find 160W chargers over Bluetooth'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        painter = context.painter
        context.print(painter.dim(f'Scanning for {args.timeout:.0f} s...'))
        chargers = await context.scan(args.timeout)
        if not chargers:
            context.print(painter.warn('No chargers found. Is Bluetooth on '
                                       'and the official app closed?'))
            return 0
        rows = [(painter.accent(charger.address), charger.name or '-',
                 f'{charger.rssi} dBm',
                 painter.good('A2687') if charger.confirmed
                 else painter.dim('possible'))
                for charger in chargers]
        context.print(render_table(('Address', 'Name', 'Signal', 'Match'),
                                   rows, painter=painter))
        context.print(painter.dim('Use one with --ble ADDRESS.'))
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--timeout', type=float,
                            default=DEFAULT_SCAN_TIMEOUT,
                            help='seconds to scan')
