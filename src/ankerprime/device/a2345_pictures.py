"""A2345 clock pictures: the cloud catalogue, own pictures, downloads.

Used by ``A2345Device``. The charger downloads a picture itself over Wi-Fi
and reports the outcome with ``0x0311``; applying a picture waits for that
report. Own pictures' links are signed for a few minutes only, so they are
listed again right before use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ankerprime.device.a2345_account import CommandWriter, SettingsSource
from ankerprime.interfaces.account import ChargerAccount
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import ClockTheme
from ankerprime.model.errors import (
    ChargerTimeoutError, InvalidParameterError, PictureTransferError)
from ankerprime.model.pictures import JPEG_MAGIC, ClockPicture
from ankerprime.protocol.a2345 import commands
from ankerprime.protocol.a2345.codes import CLOCK_THEME_CODES
from ankerprime.protocol.a2345.commands import (
    DEFAULT_TEXT_COLOR, MAX_URL_LENGTH, Command)
from ankerprime.protocol.a2345.settings import ClockState
from ankerprime.protocol.a2345.updates import ClockUpdate, ThemeDownloadUpdate


DOWNLOAD_FAILED = 0
DOWNLOAD_DONE = 1
DOWNLOAD_NEEDS_BLUETOOTH = 4

AccountSource = Callable[[Capability], ChargerAccount]


class _PendingDownload:
    """A clock picture whose download report is awaited."""

    _picture_id: int
    _future: asyncio.Future[None]

    def __init__(self, picture_id: int, future: asyncio.Future[None]):
        self._picture_id = picture_id
        self._future = future

    @property
    def picture_id(self) -> int:
        return self._picture_id

    @property
    def future(self) -> asyncio.Future[None]:
        return self._future


class PictureFeatures:
    """Clock pictures of one A2345."""

    _account: AccountSource
    _write: CommandWriter
    _device_settings: SettingsSource
    _download_timeout: float

    _catalogue: tuple[ClockPicture, ...] | None
    _download: _PendingDownload | None

    def __init__(self, account: AccountSource, *, write: CommandWriter,
                 device_settings: SettingsSource, download_timeout: float):
        self._account = account
        self._write = write
        self._device_settings = device_settings
        self._download_timeout = download_timeout
        self._catalogue = None
        self._download = None

    async def catalogue(self, language: str) -> tuple[ClockPicture, ...]:
        account = self._account(Capability.CLOCK_PICTURE)
        pictures = tuple(await account.clock_pictures(language))
        self._catalogue = pictures
        return pictures

    async def own(self) -> tuple[ClockPicture, ...]:
        account = self._account(Capability.OWN_CLOCK_PICTURES)
        return tuple(await account.own_pictures())

    async def apply(self, picture: ClockPicture) -> None:
        if picture.own:
            picture = await self._own_picture(picture.picture_id)
        command = picture_command(True, picture, toggle=False)
        # No await between this check and claiming the slot.
        if self._download is not None and not self._download.future.done():
            raise InvalidParameterError('another picture is downloading')
        future = asyncio.get_running_loop().create_future()
        download = _PendingDownload(picture.picture_id, future)
        self._download = download
        try:
            await self._write(command, include_theme=True)
            await asyncio.wait_for(future, self._download_timeout)
        except TimeoutError:
            raise ChargerTimeoutError(
                'the charger did not report the picture download') from None
        finally:
            if self._download is download:
                self._download = None

    async def upload(self, jpeg: bytes) -> None:
        account = self._account(Capability.OWN_CLOCK_PICTURES)
        if not jpeg.startswith(JPEG_MAGIC):
            raise InvalidParameterError('own pictures must be JPEG files')
        pictures = await account.own_pictures()
        if len(pictures) >= ClockPicture.OWN_LIMIT:
            raise InvalidParameterError(
                f'the account keeps at most {ClockPicture.OWN_LIMIT} '
                'own pictures')
        await account.upload_own_picture(jpeg)

    async def delete(self, picture_id: int) -> None:
        account = self._account(Capability.OWN_CLOCK_PICTURES)
        await account.delete_own_picture(picture_id)

    async def set_time(self, enabled: bool) -> None:
        """Show the time over own pictures."""

        settings = self._device_settings()
        clock = settings.clock if settings is not None else None
        expected = [ClockUpdate(clock, enabled)] if clock is not None else []
        await self._write(commands.set_image_time_display(enabled), expected,
                          include_theme=True)

    async def switch_command(self, enabled: bool, clock: ClockState,
                             theme: ClockTheme) -> tuple[Command, ClockState]:
        """``0x0205`` resending the shown picture, and the expected state."""

        picture = await self._current(clock)
        command = picture_command(enabled, picture, toggle=True)
        expected = ClockState(enabled, clock.holiday_enabled and enabled,
                              CLOCK_THEME_CODES[theme],
                              theme_id=picture.picture_id)
        return command, expected

    def on_download(self, update: ThemeDownloadUpdate) -> None:
        """Settle the awaited download from a ``0x0311`` report."""

        download = self._download
        if download is None or download.future.done():
            return
        if update.theme_id is not None and (
                update.theme_id != download.picture_id):
            return
        if update.status == DOWNLOAD_DONE:
            download.future.set_result(None)
        elif update.status == DOWNLOAD_FAILED:
            download.future.set_exception(PictureTransferError(
                'the charger could not download the picture'))
        elif update.status == DOWNLOAD_NEEDS_BLUETOOTH:
            download.future.set_exception(PictureTransferError(
                'the charger asks for the picture over Bluetooth, '
                'which this library cannot send'))

    def fail(self, error: Exception) -> None:
        """End the awaited download, the link being gone."""

        download = self._download
        if download is not None and not download.future.done():
            download.future.set_exception(error)

    async def _current(self, clock: ClockState) -> ClockPicture:
        """The picture the clock shows, looked up in the account."""

        if clock.theme_id is None:
            raise InvalidParameterError('current clock picture unknown')
        if clock.theme_code == CLOCK_THEME_CODES[ClockTheme.CUSTOM]:
            return await self._own_picture(clock.theme_id)
        catalogue = self._catalogue
        if catalogue is None:
            catalogue = await self.catalogue('en')
        for picture in catalogue:
            if picture.picture_id == clock.theme_id:
                return picture
        raise InvalidParameterError(
            f'clock picture {clock.theme_id} is not in the catalogue')

    async def _own_picture(self, picture_id: int) -> ClockPicture:
        for picture in await self.own():
            if picture.picture_id == picture_id:
                return picture
        raise InvalidParameterError(f'no own picture {picture_id}')


def picture_command(clock_on: bool, picture: ClockPicture, *,
                    toggle: bool) -> Command:
    """``0x0205`` showing a picture, as the official app builds it.

    A catalogue picture goes in the theme URL field with its text colour.
    An own picture's link goes in the long custom field, except when only
    switching the clock: then in the theme URL field if it fits.
    """

    if not picture.own:
        color = picture.text_color
        return commands.set_clock_theme(
            clock_on, ClockTheme.PICTURE, theme_id=picture.picture_id,
            image_crc=picture.crc32, url=picture.image_url,
            text_color=DEFAULT_TEXT_COLOR if color is None else color)
    url = picture.image_url
    fits = len(url.encode('utf-8')) <= MAX_URL_LENGTH
    if toggle and fits:
        return commands.set_clock_theme(
            clock_on, ClockTheme.CUSTOM, theme_id=picture.picture_id,
            image_crc=picture.crc32, url=url)
    return commands.set_clock_theme(
        clock_on, ClockTheme.CUSTOM, theme_id=picture.picture_id,
        image_crc=picture.crc32, custom_url=url)
