"""A2687 custom mode and screen picture, end to end through the simulator."""

import zlib
from collections.abc import AsyncIterator

import pytest

from ankerprime.device.a2687 import (
    A2687Device, A2687Timings, ScreenPictureTimings)
from ankerprime.model import (
    A2687, ChargingMode, ChargingProfile, CustomModeRules, CustomPortLimit,
    InvalidParameterError, OutputId, PictureTransferError, WriteRejectedError)
from ankerprime.protocol.a2687.codes import (
    OP_CHARGING_MODE, OP_COVER_TRANSFER_CHUNK, OP_COVER_TRANSFER_START,
    OP_READ_ALL, OP_SET_SCREENSAVER)
from ankerprime.session.a2687 import A2687Session
from tests.device.a2687_charger import SimulatedCharger, SimulatedLink
from tests.device.test_a2687_device import INFO, connected
from tests.utils import wait_until


PICTURE_TIMINGS = ScreenPictureTimings(
    settle_after_select=0.0, settle_after_start=0.0, chunk_spacing=0.0,
    acknowledgement_timeout=0.2, settle_before_verify=0.0,
    verify_attempts=2, verify_delay=0.01)
TIMINGS = A2687Timings(poll_interval=0.05, stale_after=0.15,
                       telemetry_deadline=0.3, opening_spacing=0.0,
                       reply_timeout=0.2, retry_max=0.05,
                       update_interval=0.2, linger=0.05, energy_save=10.0,
                       tick=0.01, screen_picture=PICTURE_TIMINGS)
# 25 slices: checkpoints after slices 9, 19 and the last.
JPEG = b'\xff\xd8\xff' + bytes(range(256)) * 15


def rules() -> CustomModeRules:
    assert A2687.custom_mode is not None
    return A2687.custom_mode


@pytest.fixture
def charger() -> SimulatedCharger:
    return SimulatedCharger()


@pytest.fixture
async def device(charger: SimulatedCharger) -> AsyncIterator[A2687Device]:
    session = A2687Session(SimulatedLink(charger), client_id='client-1',
                           time_zone=lambda: 'UTC0', stage_timeout=0.1)
    device = A2687Device(session, INFO, country='DE', timings=TIMINGS)
    yield device
    await device.disconnect()


def opcodes(charger: SimulatedCharger, start: int = 0) -> list[int]:
    return charger.opcodes()[start:]


async def test_custom_mode(device: A2687Device,
                           charger: SimulatedCharger) -> None:
    await connected(device)
    profile = rules().template(1, '').with_port(
        CustomPortLimit(OutputId.C3, 0))
    await device.apply_custom_profile(profile)
    assert charger.custom_power == bytes((1, 0, 60, 60, 0))
    applied = device.state.settings.charging_mode
    await device.exit_custom_mode()
    left = device.state.settings.charging_mode
    assert (applied, left) == (ChargingMode.CUSTOM, ChargingMode.AI)


async def test_custom_mode_limits(device: A2687Device,
                                  charger: SimulatedCharger) -> None:
    await connected(device)
    over = rules().template(1, '').with_port(CustomPortLimit(OutputId.C1, 100))
    with pytest.raises(InvalidParameterError, match='total'):
        await device.apply_custom_profile(over)
    gap = ChargingProfile(1, '', [CustomPortLimit(OutputId.C1, 10)])
    with pytest.raises(InvalidParameterError, match='c1 accepts'):
        await device.apply_custom_profile(gap)
    with pytest.raises(InvalidParameterError, match='number'):
        await device.apply_custom_profile(rules().template(2, ''))
    assert OP_CHARGING_MODE not in charger.opcodes()


async def test_screen_picture_is_written_and_shown(
        device: A2687Device, charger: SimulatedCharger) -> None:
    await connected(device)
    start = len(charger.opcodes())
    upload = await device.upload_screen_picture(
        JPEG, acknowledge_irreversible=True)
    assert upload.crc32 == zlib.crc32(JPEG)
    assert upload.chunk_count == 25 and upload.byte_count == len(JPEG)
    assert upload.previous == 0x1234 and upload.witnessed
    assert charger.stored_pictures == {upload.picture_id: upload.crc32}
    sent = opcodes(charger, start)
    assert sent[0] == OP_READ_ALL
    assert sent[1:3] == [OP_SET_SCREENSAVER, OP_COVER_TRANSFER_START]
    assert sent.count(OP_COVER_TRANSFER_CHUNK) == 25
    assert sent[-1] == OP_READ_ALL
    await wait_until(lambda: device.state.settings.display.screen_picture
                     == upload.picture_id & 0xFFFF)


async def test_pushing_the_shown_picture_again_takes_another_id(
        device: A2687Device) -> None:
    await connected(device)
    first = await device.upload_screen_picture(JPEG,
                                               acknowledge_irreversible=True)
    again = await device.upload_screen_picture(JPEG,
                                               acknowledge_irreversible=True)
    # The shown id is avoided, or the read-back could not see a change.
    assert again.picture_id == first.picture_id + 1
    assert again.previous == first.picture_id & 0xFFFF and again.witnessed


async def test_without_a_readable_screen_nothing_is_witnessed(
        device: A2687Device, charger: SimulatedCharger) -> None:
    charger.screen_picture = None
    await connected(device)
    upload = await device.upload_screen_picture(JPEG,
                                                acknowledge_irreversible=True)
    assert upload.previous is None and not upload.witnessed


async def test_upload_needs_the_acknowledgement(
        device: A2687Device, charger: SimulatedCharger) -> None:
    await connected(device)
    with pytest.raises(InvalidParameterError, match='cannot be removed'):
        await device.upload_screen_picture(JPEG,
                                           acknowledge_irreversible=False)
    with pytest.raises(InvalidParameterError, match='JPEG'):
        await device.upload_screen_picture(b'GIF89a',
                                           acknowledge_irreversible=True)
    assert OP_SET_SCREENSAVER not in charger.opcodes()


async def test_refused_start_writes_nothing(
        device: A2687Device, charger: SimulatedCharger) -> None:
    charger.cover_refusal = 0x05
    await connected(device)
    with pytest.raises(PictureTransferError, match='status 0x05') as info:
        await device.upload_screen_picture(JPEG,
                                           acknowledge_irreversible=True)
    assert not info.value.pixels_written
    assert OP_COVER_TRANSFER_CHUNK not in charger.opcodes()


async def test_unanswered_checkpoint_stops_the_transfer(
        device: A2687Device, charger: SimulatedCharger) -> None:
    charger.cover_silent_at = 9
    await connected(device)
    with pytest.raises(PictureTransferError, match='slice 9') as info:
        await device.upload_screen_picture(JPEG,
                                           acknowledge_irreversible=True)
    assert info.value.pixels_written
    assert charger.opcodes().count(OP_COVER_TRANSFER_CHUNK) == 10


async def test_lost_slices_are_detected(device: A2687Device,
                                        charger: SimulatedCharger) -> None:
    charger.cover_skip_at = 19
    await connected(device)
    with pytest.raises(PictureTransferError, match='lost') as info:
        await device.upload_screen_picture(JPEG,
                                           acknowledge_irreversible=True)
    assert info.value.pixels_written


async def test_picture_stored_but_not_shown(device: A2687Device,
                                            charger: SimulatedCharger
                                            ) -> None:
    charger.cover_hide = True
    await connected(device)
    with pytest.raises(PictureTransferError,
                       match='select_screen_picture') as info:
        await device.upload_screen_picture(JPEG,
                                           acknowledge_irreversible=True)
    assert info.value.pixels_written
    picture_id, crc = next(iter(charger.stored_pictures.items()))
    await device.select_screen_picture(picture_id, crc)
    assert charger.screen_picture == picture_id & 0xFFFF


async def test_selecting_a_missing_picture_is_refused(
        device: A2687Device) -> None:
    await connected(device)
    with pytest.raises(WriteRejectedError):
        await device.select_screen_picture(0x10001, 0xDEADBEEF)


async def test_on_demand_connection_holds_the_link(
        charger: SimulatedCharger) -> None:
    link = SimulatedLink(charger)
    session = A2687Session(link, client_id='client-1',
                           time_zone=lambda: 'UTC0', stage_timeout=0.1)
    device = A2687Device(session, INFO, keep_connection=False,
                         country='DE', timings=TIMINGS)
    try:
        await device.connect()
        start = len(charger.opcodes())
        upload = await device.upload_screen_picture(
            JPEG, acknowledge_irreversible=True)
        assert upload.witnessed
        sent = opcodes(charger, start)
        chunks = [index for index, opcode in enumerate(sent)
                  if opcode == OP_COVER_TRANSFER_CHUNK]
        # Nothing slipped in between the slices.
        between = sent[chunks[0]:chunks[-1] + 1]
        assert set(between) == {OP_COVER_TRANSFER_CHUNK}
    finally:
        await device.disconnect()
