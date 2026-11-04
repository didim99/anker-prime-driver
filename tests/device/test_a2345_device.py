"""A2345 device logic tests over a fake channel."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from ankerprime.device.a2345 import A2345Device, A2345Timings
from ankerprime.model import (
    AuthenticationError, Capability, ChargerConnectionError, ChargerState,
    ChargerTimeoutError, ChargingMode, ClockSchedule, ClockTheme, DeviceInfo,
    HourFormat, InvalidParameterError, OutputId, PortId, Schedule,
    ScheduleSlot, UnsupportedCapabilityError, Weekday, WriteRejectedError)
from tests.device.charger import (
    FakeChannel, charger_frame, charger_responder, firmware_change, wait_until)
from tests.protocol.a2345.fixtures import (
    REALTIME_OLD_FIRMWARE, STATUS_SNAPSHOT, THEME_STATUS)


TIMINGS = A2345Timings(realtime_refresh=0.05, stale_after=0.15,
                       telemetry_deadline=0.4, settings_retry=0.1,
                       readback_delay=0.02, retry_initial=0.02,
                       retry_max=0.05, tick=0.01, write_waits=(0.05, 0.1),
                       priority_waits=(0.1,))

SNAPSHOT_REQUEST = 0x0200
THEME_REQUEST = 0x0202
REALTIME_TRIGGER = 0x020B


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel(charger_responder())


@pytest.fixture
async def device(channel: FakeChannel) -> AsyncIterator[A2345Device]:
    info = DeviceInfo(serial='SERIAL123', name='Desk charger')
    device = A2345Device(channel, info, timings=TIMINGS)
    yield device
    await device.disconnect()


async def connected(device: A2345Device) -> A2345Device:
    await device.connect()
    await wait_until(lambda: device.state.available
                     and bool(device.state.outputs))
    return device


async def test_initial_state_before_connect(device: A2345Device) -> None:
    state = device.state
    assert not state.available
    assert state.info.serial == 'SERIAL123'
    assert device.last_error is None
    assert not device.connected


async def test_connect_requests_snapshot_theme_and_realtime(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    assert channel.sent_types()[:3] == [SNAPSHOT_REQUEST, THEME_REQUEST,
                                        REALTIME_TRIGGER]
    state = device.state
    assert state.available
    assert state.ports[PortId.C1].power > 0
    assert state.outputs[OutputId.C1].is_on is False
    assert state.info.name == 'Desk charger'


async def test_listeners_receive_states(device: A2345Device) -> None:
    states: list[ChargerState] = []
    unsubscribe = device.subscribe(states.append)
    await connected(device)
    assert states and states[-1] == device.state
    unsubscribe()
    count = len(states)
    await asyncio.sleep(0.1)
    assert len(states) == count


async def test_failing_listener_does_not_break_others(
        device: A2345Device) -> None:
    states: list[ChargerState] = []

    def broken(state: ChargerState) -> None:
        raise RuntimeError('boom')

    device.subscribe(broken)
    device.subscribe(states.append)
    await connected(device)
    assert states


async def test_realtime_window_is_renewed(device: A2345Device,
                                          channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await asyncio.sleep(0.2)
    assert channel.sent_types().count(REALTIME_TRIGGER) >= 2


async def test_readings_go_stale_without_telemetry(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.set_responder(None)
    await wait_until(lambda: not device.state.available, timeout=1)
    assert channel.is_open


async def test_silent_link_is_recycled(device: A2345Device,
                                       channel: FakeChannel) -> None:
    await connected(device)
    channel.set_responder(None)
    await wait_until(lambda: channel.open_count >= 2, timeout=2)
    channel.set_responder(charger_responder())
    await wait_until(lambda: device.state.available, timeout=2)


async def test_lost_link_reconnects_with_backoff(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.fail_open(ChargerConnectionError('broker down'))
    channel.lose(ChargerConnectionError('dropped'))
    await wait_until(lambda: not device.state.available)
    await wait_until(lambda: channel.open_count == 2)
    await wait_until(lambda: device.state.available)
    assert device.last_error is None


async def test_authentication_failure_stops_reconnecting(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.fail_open(AuthenticationError('token revoked'))
    channel.lose(ChargerConnectionError('dropped'))
    await wait_until(lambda: device.last_error is not None)
    assert isinstance(device.last_error, AuthenticationError)
    assert not device.state.available
    await asyncio.sleep(0.1)
    assert channel.open_count == 1


async def test_first_connect_failure_is_raised(
        device: A2345Device, channel: FakeChannel) -> None:
    channel.fail_open(ChargerConnectionError('no broker'))
    with pytest.raises(ChargerConnectionError):
        await device.connect()
    await connected(device)


async def test_disconnect_closes_and_stops(device: A2345Device,
                                           channel: FakeChannel) -> None:
    await connected(device)
    await device.disconnect()
    assert not channel.is_open
    assert not device.state.available
    await asyncio.sleep(0.1)
    assert channel.open_count == 1


async def test_refresh_requests_settings(device: A2345Device,
                                         channel: FakeChannel) -> None:
    with pytest.raises(ChargerConnectionError):
        await device.refresh()
    await connected(device)
    channel.clear()
    await device.refresh()
    assert channel.sent_types()[:2] == [SNAPSHOT_REQUEST, THEME_REQUEST]


async def test_lost_snapshot_is_requested_again(
        device: A2345Device, channel: FakeChannel) -> None:
    def realtime_only(request: object) -> list[bytes]:
        return [REALTIME_OLD_FIRMWARE]

    channel.set_responder(realtime_only)
    await device.connect()
    await asyncio.sleep(0.15)
    channel.deliver(REALTIME_OLD_FIRMWARE)
    await wait_until(lambda: channel.sent_types().count(SNAPSHOT_REQUEST) >= 2)


async def test_firmware_change_rereads_settings(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    channel.deliver(firmware_change())
    assert device.state.info.firmware_version == '2.1.1.7'
    await wait_until(lambda: SNAPSHOT_REQUEST in channel.sent_types())


async def test_theme_status_survives_snapshot(device: A2345Device,
                                              channel: FakeChannel) -> None:
    await connected(device)
    channel.deliver(THEME_STATUS)
    channel.deliver(STATUS_SNAPSHOT)
    clock = device.state.settings.clock
    assert clock is not None and clock.theme == ClockTheme.STYLE_2


async def test_write_waits_for_ack_and_applies_change(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await device.set_output(OutputId.C1, True)
    assert device.state.outputs[OutputId.C1].is_on is True
    sent = channel.sent
    assert sent[0].message_type == 0x0207
    await wait_until(lambda: SNAPSHOT_REQUEST in channel.sent_types())


async def test_write_without_ack_resends_then_times_out(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)

    def no_acks(request: object) -> list[bytes]:
        return [REALTIME_OLD_FIRMWARE]

    channel.set_responder(no_acks)
    channel.clear()
    with pytest.raises(ChargerTimeoutError):
        await device.set_output(OutputId.C2, True)
    assert channel.sent_types().count(0x0207) == 2
    assert device.state.outputs[OutputId.C2].is_on is False


async def test_priority_write_is_sent_once(device: A2345Device,
                                           channel: FakeChannel) -> None:
    await connected(device)
    channel.set_responder(lambda request: [])
    channel.clear()
    with pytest.raises(ChargerTimeoutError):
        await device.set_preferred_outputs([OutputId.C1])
    assert channel.sent_types().count(0x020C) == 1


async def test_rejected_write_raises(channel: FakeChannel) -> None:
    channel.set_responder(charger_responder(status=4))
    device = A2345Device(channel, timings=TIMINGS)
    try:
        await connected(device)
        with pytest.raises(WriteRejectedError) as exc_info:
            await device.set_brightness(50)
        assert exc_info.value.status == 4
        assert device.state.settings.display.brightness == 20
    finally:
        await device.disconnect()


async def test_lost_link_fails_pending_write(device: A2345Device,
                                             channel: FakeChannel) -> None:
    await connected(device)
    channel.set_responder(lambda request: [])

    async def lose_soon() -> None:
        await asyncio.sleep(0.02)
        channel.lose(ChargerConnectionError('dropped'))

    loser = asyncio.create_task(lose_soon())
    with pytest.raises(ChargerConnectionError):
        await device.set_output(OutputId.C3, True)
    await loser


async def test_writes_need_a_fresh_snapshot(device: A2345Device,
                                            channel: FakeChannel) -> None:
    with pytest.raises(ChargerConnectionError):
        await device.set_output(OutputId.C1, True)
    channel.set_responder(lambda request: [REALTIME_OLD_FIRMWARE])
    await device.connect()
    await wait_until(lambda: device.state.available)
    with pytest.raises(ChargerConnectionError):
        await device.set_output(OutputId.C1, True)


async def test_unsupported_capabilities(device: A2345Device) -> None:
    await connected(device)
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await device.set_auto_rotation(True)
    assert exc_info.value.capability == Capability.AUTO_ROTATION
    # The captured firmware reports no UFCS switch.
    with pytest.raises(UnsupportedCapabilityError):
        await device.set_ufcs(True)
    assert not device.supports(Capability.CUSTOM_CHARGING_MODE)


async def test_invalid_arguments(device: A2345Device) -> None:
    await connected(device)
    with pytest.raises(InvalidParameterError):
        await device.set_brightness(22)
    with pytest.raises(InvalidParameterError):
        await device.start_countdown(OutputId.C1, 0)
    with pytest.raises(InvalidParameterError):
        await device.set_charging_mode(ChargingMode.STANDARD)


async def test_countdown_start_and_cancel(device: A2345Device,
                                          channel: FakeChannel) -> None:
    await connected(device)
    await device.start_countdown(OutputId.C2, 900)
    countdown = device.state.outputs[OutputId.C2].countdown
    assert countdown is not None and countdown.total == 900
    channel.deliver(STATUS_SNAPSHOT)
    channel.clear()
    await device.cancel_countdown(OutputId.C1)
    request = channel.sent[0]
    field = request.field(0xA3)
    assert field is not None
    # Cancelling echoes the reported total of 2100 seconds.
    assert field.raw == b'\x04\x00' + (2100).to_bytes(4, 'little')
    assert device.state.outputs[OutputId.C1].countdown is None


async def test_schedule_mode_and_display_writes(device: A2345Device) -> None:
    await connected(device)
    schedule = Schedule(True, 7, 30, Weekday.MONDAY | Weekday.FRIDAY)
    await device.set_schedule(OutputId.USB_A, ScheduleSlot.POWER_ON, schedule)
    assert device.state.outputs[OutputId.USB_A].power_on_schedule == schedule
    await device.set_charging_mode(ChargingMode.PORT_PRIORITY)
    await device.set_preferred_outputs([OutputId.C2])
    state = device.state
    assert state.settings.charging_mode == ChargingMode.PORT_PRIORITY
    assert state.outputs[OutputId.C2].preferred is True
    await device.set_brightness(60)
    await device.set_hour_format(HourFormat.TWELVE_HOUR)
    await device.set_max_compatibility(True)
    display = device.state.settings.display
    assert display.brightness == 60
    assert display.hour_format == HourFormat.TWELVE_HOUR
    assert device.state.settings.max_compatibility is True


async def test_clock_writes(device: A2345Device,
                            channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await device.set_clock(True)
    frame = channel.sent[0]
    assert frame.message_type == 0x0205
    a6 = frame.field(0xA6)
    assert a6 is not None
    assert a6.raw[1:] == b'assets/img/a2345/icl_a2345_theme2.png'
    clock = device.state.settings.clock
    assert clock is not None and clock.enabled
    with pytest.raises(UnsupportedCapabilityError):
        await device.set_clock(True, ClockTheme.PICTURE)
    await device.set_clock_schedule(ClockSchedule(22, 0, 7, 0))
    await device.set_holiday_theme(True)


async def test_same_target_writes_are_serialised(
        device: A2345Device, channel: FakeChannel) -> None:
    await connected(device)
    channel.clear()
    await asyncio.gather(device.set_brightness(40), device.set_brightness(45))
    writes = [frame for frame in channel.sent if frame.message_type == 0x0204]
    assert len(writes) == 2
    assert device.state.settings.display.brightness == 45


async def test_garbage_frames_are_ignored(device: A2345Device,
                                          channel: FakeChannel) -> None:
    await connected(device)
    before = device.state
    channel.deliver(b'\x00\x01\x02')
    channel.deliver(charger_frame(0x7777, [(0xA1, b'\x01')]))
    assert device.state == before
