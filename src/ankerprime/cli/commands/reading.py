"""Reading commands: status, monitor."""

import argparse
import asyncio
import json
from datetime import datetime

from ankerprime.cli.base import ChargerCommand, CliContext
from ankerprime.cli.render import (
    render_readings, render_runtime, render_state)
from ankerprime.cli.style import Painter
from ankerprime.cli.terminal import TerminalBlock
from ankerprime.interfaces.charger import Charger
from ankerprime.model.serialize import state_to_dict
from ankerprime.model.state import ChargerState


MIN_REDRAW_INTERVAL = 0.1


class StatusCommand(ChargerCommand):
    NAME = 'status'
    HELP = 'print the full charger state once'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        if args.json:
            context.print(_to_json(charger.state))
        else:
            context.print(render_state(charger.state, _local_now(),
                                       painter=context.painter))
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--json', action='store_true',
                            help='print the state as JSON')


class MonitorCommand(ChargerCommand):
    """Live port readings, redrawn in place on a terminal.

    On a terminal the view is redrawn every interval, so the age of the last
    update and the run time keep counting while the charger is silent. Piped
    or as JSON, one state is printed per update. The energy counters it
    integrates are saved in the config folder.
    """

    NAME = 'monitor'
    HELP = 'show live readings until Ctrl+C'
    SAVES_ENERGY = True

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        if args.json or not context.ansi:
            await self._stream(context, charger, args)
        else:
            await self._redraw(context, charger, args)
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--detail', action='store_true',
                            help='show outputs and settings too, as status')
        parser.add_argument('--json', action='store_true',
                            help='print one full JSON state per line')
        parser.add_argument('--interval', type=float, default=1.0,
                            help='seconds between redraws, or the minimum '
                                 'between printed updates')
        parser.add_argument('--count', type=int, default=0,
                            help='stop after this many views (0: never)')

    async def _redraw(self, context: CliContext, charger: Charger,
                      args: argparse.Namespace) -> None:
        interval = max(args.interval, MIN_REDRAW_INTERVAL)
        started = _local_now()
        shown = 0
        with TerminalBlock(context.out) as block:
            while True:
                block.show(_view(charger.state, args.detail, started,
                                 context.painter))
                shown += 1
                if args.count and shown >= args.count:
                    return
                await asyncio.sleep(interval)

    async def _stream(self, context: CliContext, charger: Charger,
                      args: argparse.Namespace) -> None:
        updates: asyncio.Queue[ChargerState] = asyncio.Queue()
        unsubscribe = charger.subscribe(updates.put_nowait)
        started = _local_now()
        shown = 0
        try:
            state = charger.state
            while True:
                if args.json:
                    context.print(_to_json(state))
                else:
                    view = _view(state, args.detail, started,
                                 context.painter)
                    context.print(view + '\n')
                shown += 1
                if args.count and shown >= args.count:
                    return
                await asyncio.sleep(args.interval)
                state = await _latest(updates)
        finally:
            unsubscribe()


async def _latest(updates: asyncio.Queue[ChargerState]) -> ChargerState:
    """Wait for an update and skip to the newest queued one."""

    state = await updates.get()
    while not updates.empty():
        state = updates.get_nowait()
    return state


def _view(state: ChargerState, detail: bool, started: datetime,
          painter: Painter) -> str:
    now = _local_now()
    render = render_state if detail else render_readings
    return (render_runtime(started, now, painter=painter) + '\n'
            + render(state, now, painter=painter))


def _to_json(state: ChargerState) -> str:
    return json.dumps(state_to_dict(state), sort_keys=True)


def _local_now() -> datetime:
    return datetime.now().astimezone()
