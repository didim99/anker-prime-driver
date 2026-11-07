"""A2345 clock pictures: catalogue, own pictures, download reports."""

import asyncio
import zlib
from collections.abc import AsyncIterator

import pytest

from ankerprime.device.a2345 import A2345Device
from ankerprime.model import (
    Capability, ChargerConnectionError, ChargerTimeoutError,
    ClockPicture, ClockTheme, DeviceInfo, InvalidParameterError,
    PictureTransferError, UnsupportedCapabilityError)
from ankerprime.protocol.a2345.frame import Frame
from ankerprime.utils import u32le
from tests.device.account import FakeAccount
from tests.device.charger import (
    FakeChannel, Responder, charger_frame, charger_responder)
from tests.device.test_a2345_device import connected
from tests.device.timings import A2345_FAST
from tests.utils import wait_until


CLOCK_THEME = 0x0205
IMAGE_TIME_DISPLAY = 0x0222
FOREST = ClockPicture(31, 'https://cdn/forest.jpg', 0x1A2B3C4D,
                      title='Forest', text_color=0xFF00FF00)
SEA = ClockPicture(32, 'https://cdn/sea.jpg', 0xBEEF, title='Sea')
PICTURE_TIMINGS = A2345_FAST.replace(picture_download=0.2)


def download_report(picture_id: int, status: int, code: int = 3) -> bytes:
    return charger_frame(0x0311, [(0xA2, b'\x03' + u32le(picture_id)),
                                  (0xA3, bytes((1, status))),
                                  (0xA4, bytes((1, code)))])


def reporting_responder(status: int | None) -> Responder:
    """Acks writes; a picture theme write is followed by a download report."""

    base = charger_responder()

    def respond(request: Frame) -> list[bytes]:
        replies = base(request)
        theme = request.field(0xA2)
        if (request.message_type == CLOCK_THEME and status is not None
                and theme is not None and theme.raw[-1] & 0x7F in (3, 5)):
            picture = request.field(0xA3)
            assert picture is not None
            picture_id = int.from_bytes(picture.raw[1:5], 'little')
            code = theme.raw[-1] & 0x7F
            replies.append(download_report(picture_id, 2, code))
            replies.append(download_report(picture_id, status, code))
        return replies

    return respond


@pytest.fixture
def account() -> FakeAccount:
    account = FakeAccount()
    account.catalogue.extend([FOREST, SEA])
    return account


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel(reporting_responder(1))


@pytest.fixture
async def device(channel: FakeChannel, account: FakeAccount
                 ) -> AsyncIterator[A2345Device]:
    device = A2345Device(channel, DeviceInfo(serial='SERIAL123'),
                         account=account, timings=PICTURE_TIMINGS)
    yield device
    await device.disconnect()


def theme_frames(channel: FakeChannel) -> list[Frame]:
    return [frame for frame in channel.sent
            if frame.message_type == CLOCK_THEME]


def field(frame: Frame, tag: int) -> bytes:
    value = frame.field(tag)
    assert value is not None
    return value.raw[1:]


async def test_capabilities_follow_the_account_and_firmware(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    assert device.supports(Capability.CLOCK_PICTURE)
    assert device.supports(Capability.OWN_CLOCK_PICTURES)
    plain = A2345Device(channel, timings=PICTURE_TIMINGS)
    assert not plain.supports(Capability.CLOCK_PICTURE)
    with pytest.raises(UnsupportedCapabilityError):
        await plain.clock_pictures()


async def test_catalogue(device: A2345Device, account: FakeAccount) -> None:
    await connected(device)
    assert await device.clock_pictures('zh') == (FOREST, SEA)
    assert ('clock_pictures', 'zh') in account.calls


async def test_apply_catalogue_picture_waits_for_the_download(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await device.apply_clock_picture(FOREST)
    frame = theme_frames(channel)[0]
    assert field(frame, 0xA2) == bytes((0x83,))
    assert field(frame, 0xA3) == u32le(31)
    assert field(frame, 0xA4) == u32le(0x1A2B3C4D)
    assert field(frame, 0xA5) == u32le(0xFF00FF00)
    assert field(frame, 0xA6) == b'https://cdn/forest.jpg'
    assert frame.field(0xA7) is None
    clock = device.state.settings.clock
    assert clock is not None
    assert clock.theme == ClockTheme.PICTURE and clock.picture_id == 31


async def test_default_text_colour(device: A2345Device,
                                   channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await device.apply_clock_picture(SEA)
    assert field(theme_frames(channel)[0], 0xA5) == u32le(0xFFFFFFFF)


@pytest.mark.parametrize('status, message', [
    (0, 'could not download'), (4, 'Bluetooth')])
async def test_failed_download(account: FakeAccount, status: int,
                               message: str) -> None:
    channel = FakeChannel(reporting_responder(status))
    device = A2345Device(channel, account=account, timings=PICTURE_TIMINGS)
    try:
        await connected(device)
        with pytest.raises(PictureTransferError, match=message):
            await device.apply_clock_picture(FOREST)
    finally:
        await device.disconnect()


async def test_missing_report_times_out(account: FakeAccount) -> None:
    channel = FakeChannel(reporting_responder(None))
    device = A2345Device(channel, account=account, timings=PICTURE_TIMINGS)
    try:
        await connected(device)
        with pytest.raises(ChargerTimeoutError, match='download'):
            await device.apply_clock_picture(FOREST)
    finally:
        await device.disconnect()


async def test_lost_link_ends_the_wait(account: FakeAccount) -> None:
    channel = FakeChannel(reporting_responder(None))
    device = A2345Device(channel, account=account, timings=PICTURE_TIMINGS)
    try:
        await connected(device)
        task = asyncio.create_task(device.apply_clock_picture(FOREST))
        await wait_until(lambda: bool(theme_frames(channel)))
        await asyncio.sleep(0.02)
        channel.lose(ChargerConnectionError('gone'))
        with pytest.raises(ChargerConnectionError):
            await task
    finally:
        await device.disconnect()


async def test_own_picture_uses_a_fresh_link(device: A2345Device,
                                             account: FakeAccount,
                                             channel: FakeChannel) -> None:
    await connected(device)
    jpeg = b'\xff\xd8\xff' + b'x' * 50
    await device.upload_clock_picture(jpeg)
    own = await device.own_clock_pictures()
    assert len(own) == 1 and own[0].own
    stale = ClockPicture(own[0].picture_id, 'https://expired', 0, own=True)
    channel.clear()
    await device.apply_clock_picture(stale)
    frame = theme_frames(channel)[0]
    assert field(frame, 0xA2) == bytes((0x85,))
    assert field(frame, 0xA4) == u32le(zlib.crc32(jpeg))
    # The apply path sends an own picture's link in the long field only.
    assert frame.field(0xA6) is None
    assert field(frame, 0xA7) == own[0].image_url.encode()
    await device.delete_clock_picture(own[0].picture_id)
    with pytest.raises(InvalidParameterError, match='no own picture'):
        await device.apply_clock_picture(own[0])


async def test_upload_checks(device: A2345Device,
                             account: FakeAccount) -> None:
    await connected(device)
    with pytest.raises(InvalidParameterError, match='JPEG'):
        await device.upload_clock_picture(b'\x89PNG')
    for _ in range(ClockPicture.OWN_LIMIT):
        await device.upload_clock_picture(b'\xff\xd8\xff')
    with pytest.raises(InvalidParameterError, match='at most 8'):
        await device.upload_clock_picture(b'\xff\xd8\xff')


async def test_switching_the_clock_resends_the_picture(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    await connected(device)
    await device.apply_clock_picture(FOREST)
    await asyncio.sleep(0.1)
    # The charger reports the picture (the snapshot read-back does not).
    channel.deliver(download_report(31, 1))
    channel.clear()
    await device.set_clock(False)
    frame = theme_frames(channel)[0]
    assert field(frame, 0xA2) == bytes((0x03,))
    assert field(frame, 0xA3) == u32le(31)
    assert field(frame, 0xA6) == b'https://cdn/forest.jpg'


async def test_switching_an_own_picture_uses_the_short_field(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    await connected(device)
    await device.upload_clock_picture(b'\xff\xd8\xff')
    picture = (await device.own_clock_pictures())[0]
    channel.deliver(download_report(picture.picture_id, 1, code=5))
    channel.clear()
    await device.set_clock(True)
    frame = theme_frames(channel)[0]
    assert field(frame, 0xA2) == bytes((0x85,))
    assert field(frame, 0xA6) == picture.image_url.encode()
    assert frame.field(0xA7) is None


async def test_picture_time(device: A2345Device,
                            channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await device.set_picture_time(False)
    assert channel.sent[0].message_type == IMAGE_TIME_DISPLAY
    clock = device.state.settings.clock
    assert clock is not None and clock.picture_time is False
