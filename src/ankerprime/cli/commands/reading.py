"""Reading commands: status, monitor."""

import argparse
import asyncio
import json

from ankerprime.cli.base import ChargerCommand, CliContext
from ankerprime.cli.render import render_state
from ankerprime.interfaces.charger import Charger
from ankerprime.model.serialize import state_to_dict
from ankerprime.model.state import ChargerState
from ankerprime.utils import utc_now


CLEAR_SCREEN = '\x1b[H\x1b[2J'


class StatusCommand(ChargerCommand):
    NAME = 'status'
    HELP = 'print the full charger state once'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        context.print(_render(charger.state, args.json))
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--json', action='store_true',
                            help='print the state as JSON')


class MonitorCommand(ChargerCommand):
    """Prints the state on every update until interrupted."""

    NAME = 'monitor'
    HELP = 'show live readings until Ctrl+C'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        updates: asyncio.Queue[ChargerState] = asyncio.Queue()
        unsubscribe = charger.subscribe(updates.put_nowait)
        redraw = not args.json and context.out.isatty()
        shown = 0
        try:
            state = charger.state
            while True:
                text = _render(state, args.json)
                context.print(CLEAR_SCREEN + text if redraw else text)
                shown += 1
                if args.count and shown >= args.count:
                    return 0
                await asyncio.sleep(args.interval)
                state = await _latest(updates)
        finally:
            unsubscribe()

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--json', action='store_true',
                            help='print one JSON state per line')
        parser.add_argument('--interval', type=float, default=1.0,
                            help='minimum seconds between updates')
        parser.add_argument('--count', type=int, default=0,
                            help='stop after this many updates (0: never)')


async def _latest(updates: asyncio.Queue[ChargerState]) -> ChargerState:
    """Wait for an update and skip to the newest queued one."""

    state = await updates.get()
    while not updates.empty():
        state = updates.get_nowait()
    return state


def _render(state: ChargerState, as_json: bool) -> str:
    if as_json:
        return json.dumps(state_to_dict(state), sort_keys=True)
    return render_state(state, utc_now())
