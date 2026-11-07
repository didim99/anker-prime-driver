"""Pictures: the 250W's clock pictures and the 160W's screen picture."""

import argparse
import asyncio
from pathlib import Path

from ankerprime.cli.base import Action, ChargerActionCommand, CliContext
from ankerprime.cli.commands.control import confirm
from ankerprime.cli.parsing import parse_hex_u32, parse_switch
from ankerprime.cli.render import render_table
from ankerprime.cli.style import Painter
from ankerprime.interfaces.charger import Charger
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import ClockTheme
from ankerprime.model.errors import (
    InvalidParameterError, PictureTransferError)
from ankerprime.model.pictures import ClockPicture
from ankerprime.pictures import CLOCK_PICTURE, SCREEN_PICTURE, prepare_picture


LANGUAGES = ('en', 'zh')
IRREVERSIBLE = ('The picture stays in the charger: no Bluetooth command '
                'removes it (it keeps four; a fifth replaces the oldest).')


def _list_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--own', action='store_true',
                        help="the account's own pictures")
    parser.add_argument('--language', choices=LANGUAGES, default='en',
                        help='catalogue titles (default: en)')


def _apply_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('id', type=int)
    parser.add_argument('--own', action='store_true',
                        help='an own picture rather than a catalogue one')


def _upload_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('file', help='any image Pillow reads; it is '
                                     'cropped to fill 480x200')


def _id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('id', type=int)


def _time_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('on', type=parse_switch, help='on or off')


def _screen_upload_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('file')
    parser.add_argument('--i-understand', action='store_true',
                        help='confirm the picture cannot be removed from '
                             'the charger')


def _select_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('id', type=int)
    parser.add_argument('crc32', type=parse_hex_u32,
                        help='CRC-32 printed by upload, in hex')


class ClockPictureCommand(ChargerActionCommand):
    """Lists, applies, uploads and deletes clock pictures."""

    NAME = 'clock-picture'
    HELP = 'show cloud or own pictures behind the clock (250W)'
    DEFAULT_ACTION = 'list'
    ACTIONS = (
        Action('list', 'list the catalogue or the own pictures',
               _list_arguments),
        Action('apply', 'show a picture', _apply_arguments),
        Action('upload', 'add an own picture from an image file',
               _upload_arguments),
        Action('delete', 'remove an own picture', _id_argument),
        Action('time', 'show the time over own pictures', _time_arguments),
    )

    async def run_list(self, context: CliContext, charger: Charger,
                       args: argparse.Namespace) -> int:
        pictures = await _pictures(charger, args.own, args.language)
        context.print(render_pictures(charger, pictures, args.own,
                                      context.painter))
        return 0

    async def run_apply(self, context: CliContext, charger: Charger,
                        args: argparse.Namespace) -> int:
        pictures = await _pictures(charger, args.own, 'en')
        picture = _find(pictures, args.id, args.own)
        context.print(f'Waiting for the charger to download '
                      f'"{picture.title or picture.picture_id}"…')
        await charger.apply_clock_picture(picture)
        capability = (Capability.OWN_CLOCK_PICTURES if args.own
                      else Capability.CLOCK_PICTURE)
        confirm(context, charger, capability,
                'Picture shown; clock switched on.')
        return 0

    async def run_upload(self, context: CliContext, charger: Charger,
                         args: argparse.Namespace) -> int:
        data = await asyncio.to_thread(Path(args.file).read_bytes)
        jpeg = await asyncio.to_thread(prepare_picture, data, CLOCK_PICTURE)
        await charger.upload_clock_picture(jpeg)
        confirm(context, charger, Capability.OWN_CLOCK_PICTURES,
                f'Picture uploaded ({len(jpeg) // 1024} KB); show it with '
                '"clock-picture apply --own ID".')
        return 0

    async def run_delete(self, context: CliContext, charger: Charger,
                         args: argparse.Namespace) -> int:
        await charger.delete_clock_picture(args.id)
        confirm(context, charger, Capability.OWN_CLOCK_PICTURES,
                f'Own picture {args.id} deleted.')
        return 0

    async def run_time(self, context: CliContext, charger: Charger,
                       args: argparse.Namespace) -> int:
        await charger.set_picture_time(args.on)
        state = 'on' if args.on else 'off'
        confirm(context, charger, Capability.OWN_CLOCK_PICTURES,
                f'Time over own pictures switched {state}.')
        return 0

    def add_charger_arguments(self, parser: argparse.ArgumentParser) -> None:
        # Without an action the catalogue is listed.
        parser.set_defaults(own=False, language='en')
        super().add_charger_arguments(parser)


class ScreenPictureCommand(ChargerActionCommand):
    """Writes a picture to the 160W's own screen, or shows a stored one."""

    NAME = 'screen-picture'
    HELP = "put a picture on the charger's screen (160W; cannot be undone)"
    ACTIONS = (
        Action('upload', 'write an image file to the screen; it is cropped '
                         'to fill 240x240', _screen_upload_arguments),
        Action('select', 'show a picture written before (reversible)',
               _select_arguments),
    )

    async def run_select(self, context: CliContext, charger: Charger,
                         args: argparse.Namespace) -> int:
        await charger.select_screen_picture(args.id, args.crc32)
        confirm(context, charger, Capability.SCREEN_PICTURE,
                f'Screen picture {args.id} shown.')
        return 0

    async def run_upload(self, context: CliContext, charger: Charger,
                         args: argparse.Namespace) -> int:
        painter = context.painter
        if not args.i_understand:
            context.print(painter.warn(IRREVERSIBLE))
            raise InvalidParameterError(
                'add --i-understand to write the picture anyway')
        data = await asyncio.to_thread(Path(args.file).read_bytes)
        jpeg = await asyncio.to_thread(prepare_picture, data, SCREEN_PICTURE)
        context.print(f'Sending {len(jpeg) // 1024} KB to the charger…')
        try:
            upload = await charger.upload_screen_picture(
                jpeg, acknowledge_irreversible=True)
        except PictureTransferError as error:
            if error.pixels_written:
                context.print(painter.warn(
                    'Part or all of the picture is in the charger now.'))
            raise
        message = (f'Screen picture shown: id {upload.picture_id}, '
                   f'CRC-32 {upload.crc32:08x}.')
        if not upload.witnessed:
            message += (' The screen could not be compared with before; '
                        'check it.')
        confirm(context, charger, Capability.SCREEN_PICTURE, message)
        return 0


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

