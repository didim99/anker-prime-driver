"""Account commands: login, logout, regions, devices."""

import argparse
import getpass
import sys

from ankerprime.cli.base import CliContext, Command
from ankerprime.cli.render import render_devices, render_table
from ankerprime.cloud.auth import AccountClient, AccountCredentials
from ankerprime.cloud.regions import REGIONS, PHONE_REGIONS, server_for
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
        if country in PHONE_REGIONS:
            raise InvalidParameterError('mainland China accounts sign in '
                                        'with "ankerprime login-phone"')
        password = _read_password(args.password_stdin)
        credentials = AccountCredentials(args.email, password, country)
        async with context.http() as http:
            authentication = await AccountClient(http).login(
                credentials.email, credentials.password, credentials.country)
        kept = None if args.no_save_password else credentials
        await context.config.save_login(authentication, kept)
        painter = context.painter
        context.print(painter.good(f'Signed in as user {authentication.user_id}'
                                   f' on {authentication.server}.'))
        context.print(painter.dim('Login stored in '
                                  f'{context.config.directory}.'))
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


class PhoneLoginCommand(Command):
    """Signs in a mainland China account with an SMS code.

    Sends the code and asks for it; ``--send-only`` and ``--code`` split the
    two steps for scripts. Only the token is stored: when it expires, sign
    in again with a new code.
    """

    NAME = 'login-phone'
    HELP = 'sign in to a mainland China account with an SMS code'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        painter = context.painter
        async with context.http() as http:
            client = AccountClient(http)
            code = args.code
            if code is None:
                await client.request_phone_code(args.phone)
                context.print(f'SMS code sent to {args.phone}.')
                if args.send_only:
                    return 0
                code = _read_code(args.code_stdin)
            authentication = await client.login_phone(args.phone, code)
        await context.config.save_login(authentication, None)
        context.print(painter.good(f'Signed in as user {authentication.user_id}'
                                   f' on {authentication.server}.'))
        context.print(painter.dim('Token stored in '
                                  f'{context.config.directory}; sign in '
                                  'again with a new code when it expires.'))
        return 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('phone', help='mainland China mobile number '
                                          '(11 digits)')
        step = parser.add_mutually_exclusive_group()
        step.add_argument('--send-only', action='store_true',
                          help='only send the SMS code')
        step.add_argument('--code', help='sign in with a code received '
                                         'before (after --send-only)')
        parser.add_argument('--code-stdin', action='store_true',
                            help='read the code from standard input')


class LogoutCommand(Command):
    NAME = 'logout'
    HELP = 'remove the stored login'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        await context.config.clear()
        context.print(context.painter.good('Stored login removed.'))
        return 0


class RegionsCommand(Command):
    NAME = 'regions'
    HELP = 'list the country codes Anker accepts'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        painter = context.painter
        rows = []
        for code, name in REGIONS.items():
            host = server_for(code).removeprefix('https://')
            if code in PHONE_REGIONS:
                rows.append((painter.accent(code), name,
                             host + painter.dim(' (SMS login: login-phone)')))
            else:
                rows.append((painter.accent(code), name, host))
        context.print(render_table(('Code', 'Country', 'Server'), rows,
                                   painter=painter))
        return 0


class DevicesCommand(Command):
    NAME = 'devices'
    HELP = 'list the devices bound to the account'

    async def run(self, context: CliContext,
                  args: argparse.Namespace) -> int:
        async with context.account() as account:
            devices = await account.bound_devices()
        context.print(render_devices(devices, painter=context.painter))
        return 0


def _read_code(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().strip()
    return input('SMS code: ').strip()


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip('\r\n')
    return getpass.getpass('Anker password: ')
