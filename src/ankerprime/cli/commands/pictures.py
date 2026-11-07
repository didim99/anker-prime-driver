"""Clock pictures of the 250W: catalogue, own pictures, time overlay."""

import argparse
import asyncio
from pathlib import Path

from ankerprime.cli.base import ChargerCommand, CliContext
from ankerprime.cli.commands.control import confirm
from ankerprime.cli.parsing import parse_switch
from ankerprime.cli.render import render_table
from ankerprime.cli.style import Painter
from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import ClockTheme
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.pictures import ClockPicture
from ankerprime.pictures import CLOCK_PICTURE, prepare_picture


LANGUAGES = ('en', 'zh')


class ClockPictureCommand(ChargerCommand):
    """Lists, applies, uploads and deletes clock pictures."""

    NAME = 'clock-picture'
    HELP = 'show cloud or own pictures behind the clock (250W)'

    async def run_charger(self, context: CliContext, charger: Charger,
                          args: argparse.Namespace) -> int:
        match args.action or 'list':
            case 'list':
                pictures = await _pictures(charger, args.own, args.language)
                context.print(render_pictures(charger, pictures, args.own,
                                              context.painter))
                return 0
            case 'apply':
                pictures = await _pictures(charger, args.own, 'en')
                picture = _find(pictures, args.id, args.own)
                context.print(f'Waiting for the charger to download '
                              f'"{picture.title or picture.picture_id}"…')
                await charger.apply_clock_picture(picture)
                capability = (Capability.OWN_CLOCK_PICTURES if args.own
                              else Capability.CLOCK_PICTURE)
                message = 'Picture shown; clock switched on.'
            case 'upload':
                data = await asyncio.to_thread(Path(args.file).read_bytes)
                jpeg = await asyncio.to_thread(prepare_picture, data,
                                               CLOCK_PICTURE)
                await charger.upload_clock_picture(jpeg)
                capability = Capability.OWN_CLOCK_PICTURES
                message = (f'Picture uploaded ({len(jpeg) // 1024} KB); '
                           'show it with "clock-picture apply --own ID".')
            case 'delete':
                await charger.delete_clock_picture(args.id)
                capability = Capability.OWN_CLOCK_PICTURES
                message = f'Own picture {args.id} deleted.'
            case _:
                await charger.set_picture_time(args.on)
                capability = Capability.OWN_CLOCK_PICTURES
                state = 'on' if args.on else 'off'
                message = f'Time over own pictures switched {state}.'
        confirm(context, charger, capability, message)
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        # Without an action the catalogue is listed.
        parser.set_defaults(own=False, language='en')
        actions = parser.add_subparsers(dest='action', metavar='action')
        listing = actions.add_parser(
            'list', help='list the catalogue or the own pictures')
        listing.add_argument('--own', action='store_true',
                             help="the account's own pictures")
        listing.add_argument('--language', choices=LANGUAGES, default='en',
                             help='catalogue titles (default: en)')
        apply = actions.add_parser('apply', help='show a picture')
        apply.add_argument('id', type=int)
        apply.add_argument('--own', action='store_true',
                           help='an own picture rather than a catalogue one')
        upload = actions.add_parser(
            'upload', help='add an own picture from an image file')
        upload.add_argument('file', help='any image Pillow reads; it is '
                                         'cropped to fill 480x200')
        delete = actions.add_parser('delete', help='remove an own picture')
        delete.add_argument('id', type=int)
        time = actions.add_parser('time',
                                  help='show the time over own pictures')
        time.add_argument('on', type=parse_switch, help='on or off')


def render_pictures(charger: Charger, pictures: tuple[ClockPicture, ...],
                    own: bool, painter: Painter) -> str:
    if not pictures:
        return ('No own pictures; add one with "ankerprime clock-picture '
                'upload FILE".' if own else 'The catalogue is empty.')
    clock = charger.state.settings.clock
    current = clock.picture_id if clock is not None else None
    shown_own = clock is not None and clock.theme == ClockTheme.CUSTOM
    rows = []
    for picture in pictures:
        shown = picture.picture_id == current and shown_own == own
        rows.append((painter.good('*') if shown else '',
                     str(picture.picture_id),
                     painter.accent(picture.title or '-'),
                     picture.category))
    header = ('', 'ID', 'Title', '' if own else 'Category')
    lines = [render_table(header, rows, painter=painter)]
    if own:
        lines.append(painter.dim(f'{len(pictures)} of '
                                 f'{ClockPicture.OWN_LIMIT} own pictures'))
    return '\n'.join(lines)


async def _pictures(charger: Charger, own: bool, language: str
                    ) -> tuple[ClockPicture, ...]:
    if own:
        return await charger.own_clock_pictures()
    return await charger.clock_pictures(language)


def _find(pictures: tuple[ClockPicture, ...], picture_id: int,
          own: bool) -> ClockPicture:
    for picture in pictures:
        if picture.picture_id == picture_id:
            return picture
    kind = 'own picture' if own else 'catalogue picture'
    raise InvalidParameterError(f'no {kind} {picture_id}')

