"""Account commands: login, logout, regions, devices."""

import argparse
import getpass
import sys

from ankerprime.cli.base import CliContext, Command
from ankerprime.cli.render import render_devices, render_table
from ankerprime.cloud.auth import AccountClient, AccountCredentials
from ankerprime.cloud.regions import REGIONS, UNSUPPORTED_REGIONS, server_for
from ankerprime.model.errors import InvalidParameterError


class LoginCommand(Command):
    """Signs in and stores the token, and by default the credentials."""

    NAME = 'login'
    HELP = 'sign in to the Anker account and store the login'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        country = args.country.upper()
        if country not in REGIONS:
            raise InvalidParameterError(f'unknown country code {args.country}; '
                                        f'see "ankerprime regions"')
        password = _read_password(args.password_stdin)
        credentials = AccountCredentials(args.email, password, country)
        async with context.http() as http:
            authentication = await AccountClient(http).login(
                credentials.email, credentials.password, credentials.country)
        kept = None if args.no_save_password else credentials
        await context.config.save_login(authentication, kept)
        context.print(f'Signed in as user {authentication.user_id} '
                      f'on {authentication.server}.')
        context.print(f'Login stored in {context.config.directory}.')
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('email', help='Anker account email')
        parser.add_argument('country',
                            help='account country code, e.g. DE or US')
        parser.add_argument('--password-stdin', action='store_true',
                            help='read the password from standard input')
        parser.add_argument('--no-save-password', action='store_true',
                            help='store only the token; sign in again when '
                                 'it expires')


class LogoutCommand(Command):
    NAME = 'logout'
    HELP = 'remove the stored login'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        await context.config.clear()
        context.print('Stored login removed.')
        return 0


class RegionsCommand(Command):
    NAME = 'regions'
    HELP = 'list the country codes Anker accepts'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        rows = []
        for code, name in REGIONS.items():
            host = server_for(code).removeprefix('https://')
            if code in UNSUPPORTED_REGIONS:
                host += ' (SMS login, unsupported)'
            rows.append((code, name, host))
        context.print(render_table(('Code', 'Country', 'Server'), rows))
        return 0


class DevicesCommand(Command):
    NAME = 'devices'
    HELP = 'list the devices bound to the account'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        async with context.account() as account:
            devices = await account.bound_devices()
        context.print(render_devices(devices))
        return 0


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip('\r\n')
    return getpass.getpass('Anker password: ')
