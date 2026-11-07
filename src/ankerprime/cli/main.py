"""Command-line entry point."""

import argparse
import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

import aiohttp

from ankerprime import __version__
from ankerprime.cli.base import (
    BleScanner, BleTransportFactory, CliContext, Command, HttpFactory)
from ankerprime.cli.commands import COMMANDS
from ankerprime.cli.config import CliConfig, default_config_dir
from ankerprime.cli.style import ColorMode, Painter, ansi_terminal, use_color
from ankerprime.cloud.http import AiohttpClient
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.mqtt import MqttConnector
from ankerprime.model.errors import (
    AccountRefusedError, AnkerPrimeError, AuthenticationError)
from ankerprime.transport.cloud import TlsFactory


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTHENTICATION = 3
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ankerprime',
        description='Monitor and control Anker Prime chargers.')
    parser.add_argument('--version', action='version',
                        version=f'%(prog)s {__version__}')
    parser.add_argument('--config-dir', type=Path,
                        help='where the login is stored (default: '
                             f'{default_config_dir()})')
    parser.add_argument('--debug', action='store_true',
                        help='log library details (identifiers are shortened)')
    parser.add_argument('--color', type=ColorMode, default=ColorMode.AUTO,
                        choices=list(ColorMode),
                        help='colour the output (default: auto, for '
                             'terminals unless NO_COLOR is set)')
    subparsers = parser.add_subparsers(dest='command', metavar='command')
    for name, command_class in COMMANDS.items():
        command = command_class()
        subparser = subparsers.add_parser(name, help=command.HELP,
                                          description=command.HELP)
        command.add_arguments(subparser)
        subparser.set_defaults(handler=command)
    return parser


def main(argv: list[str] | None = None, *,
         http_factory: HttpFactory | None = None,
         connector: MqttConnector | None = None,
         tls_factory: TlsFactory | None = None,
         ble_transport: BleTransportFactory | None = None,
         ble_scanner: BleScanner | None = None,
         out: TextIO | None = None) -> int:
    """Run the command-line interface and return the process exit code.

    The keyword arguments replace the network and Bluetooth for tests.
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(out)
        return EXIT_OK
    _configure_logging(args.debug)
    output = out or sys.stdout
    errors = Painter(use_color(args.color, sys.stderr))
    config = CliConfig(args.config_dir or default_config_dir())
    ansi = ansi_terminal(output)
    context = CliContext(config, http_factory or aiohttp_client, output,
                         connector=connector, tls_factory=tls_factory,
                         ble_transport=ble_transport, ble_scanner=ble_scanner,
                         painter=Painter(use_color(args.color, output)),
                         ansi=ansi)
    try:
        command: Command = args.handler
        with asyncio.Runner(loop_factory=_loop_factory()) as runner:
            return runner.run(command.run(context, args))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except AuthenticationError as error:
        print(f'{errors.bad("error:")} {error}', file=sys.stderr)
        hint = ('run "ankerprime login" again ("login-phone" for mainland '
                'China)')
        if isinstance(error, AccountRefusedError):
            hint = ('pass --owner-id with the Anker account ID the charger '
                    'is bound to')
        print(errors.warn(f'hint: {hint}'), file=sys.stderr)
        return EXIT_AUTHENTICATION
    except AnkerPrimeError as error:
        print(f'{errors.bad("error:")} {error}', file=sys.stderr)
        return EXIT_ERROR


@asynccontextmanager
async def aiohttp_client() -> AsyncIterator[HttpClient]:
    async with aiohttp.ClientSession() as session:
        yield AiohttpClient(session)


def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s: '
                               '%(message)s')
    logging.getLogger('ankerprime').setLevel(level)


def _loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    """aiomqtt needs a selector loop; Windows defaults to a proactor loop."""

    if sys.platform == 'win32':
        return asyncio.SelectorEventLoop
    return None
