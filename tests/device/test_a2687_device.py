"""A2687 device tests, end to end through the session and the simulator."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from ankerprime.device.a2687 import A2687Device
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import (
    ChargingMode, DisplayLanguage, FixedAllocation,
    OutputId, PortId, ScreenOrientation, ScreenTimeout)
from ankerprime.model.errors import (
    AuthenticationError, ChargerConnectionError, ChargerTimeoutError,
    InvalidParameterError, UnsupportedCapabilityError, WriteRejectedError)
from ankerprime.model.state import DeviceInfo
from ankerprime.protocol.a2687.codes import (
    OP_BIND_SUCCESS, OP_BRIGHTNESS, OP_CHARGING_MODE,
    OP_PORT_TIMER, OP_READ_ALL, OP_REALTIME_TRIGGER, OP_USER_AUTH)
from ankerprime.session.a2687 import A2687Session
from tests.device.a2687_charger import SimulatedCharger, SimulatedLink
from tests.device.timings import A2687_FAST
from tests.utils import MemoryStore, wait_until


INFO = DeviceInfo(name='Desk charger')


@pytest.fixture
def charger() -> SimulatedCharger:
    return SimulatedCharger()


@pytest.fixture
def link(charger: SimulatedCharger) -> SimulatedLink:
    return SimulatedLink(charger)


def make_device(link: SimulatedLink, *, keep_connection: bool = True,
                owner_id: str | None = None,
                energy_store: MemoryStore | None = None) -> A2687Device:
    session = A2687Session(link, client_id='client-1', owner_id=owner_id,
                           time_zone=lambda: 'UTC0', stage_timeout=0.1)
    return A2687Device(session, INFO, keep_connection=keep_connection,
                       country='DE', owner_id=owner_id,
                       energy_store=energy_store, timings=A2687_FAST)


@pytest.fixture
async def device(link: SimulatedLink) -> AsyncIterator[A2687Device]:
    device = make_device(link)
    yield device
    await device.disconnect()


async def connected(device: A2687Device) -> A2687Device:
    await device.connect()
    await wait_until(lambda: device.state.available
                     and device.state.settings.display.brightness is not None)
    return device


async def test_initial_state_before_connect(device: A2687Device) -> None:
    state = device.state
    assert not state.available
    assert state.info.name == 'Desk charger'
    assert state.product.code == 'A2687'
    assert not device.connected
    assert device.keep_connection


async def test_connect_negotiates_and_reports(device: A2687Device,
                                              charger: SimulatedCharger
                                              ) -> None:
    await connected(device)
    session_requests = [opcode for opcode in charger.opcodes()
                        if opcode >= OP_READ_ALL]
    assert session_requests[:3] == [OP_READ_ALL, OP_BIND_SUCCESS,
                                    OP_REALTIME_TRIGGER]
    bind = dict(charger.requests)[OP_BIND_SUCCESS]
    assert bind.get(0xA2) == b'\x04DE'
    state = device.state
    assert state.info.serial == 'ASHDMOCK00000000'
    assert state.info.firmware_version == 'v0.0.5.0'
    assert state.info.name == 'Desk charger'
    assert state.ports[PortId.C1].power == pytest.approx(65.0)
    assert state.outputs[OutputId.C1].is_on
    assert not state.outputs[OutputId.C3].is_on
    assert state.settings.charging_mode == ChargingMode.AI
    assert state.settings.display.screen_timeout == ScreenTimeout.ONE_MINUTE
    assert device.connected


async def test_owner_id_is_used_for_auth_and_probe(link: SimulatedLink,
                                                   charger: SimulatedCharger
                                                   ) -> None:
    charger.required_identity = 'owner-1'
    device = make_device(link, owner_id='owner-1')
    try:
        await connected(device)
        bind = dict(charger.requests)[OP_BIND_SUCCESS]
        assert bind.get(0xA3) == b'\x04owner-1'
    finally:
        await device.disconnect()


async def test_polls_while_connected(device: A2687Device,
                                     charger: SimulatedCharger) -> None:
    await connected(device)
    charger.requests.clear()
    await asyncio.sleep(0.25)
    opcodes = charger.opcodes()
    assert opcodes.count(OP_BIND_SUCCESS) >= 3
    assert 1 <= opcodes.count(OP_READ_ALL) < opcodes.count(OP_BIND_SUCCESS)


async def test_readings_go_stale_and_silent_link_is_recycled(
        device: A2687Device, charger: SimulatedCharger,
        link: SimulatedLink) -> None:
    await connected(device)
    charger.muted = True
    await wait_until(lambda: not device.state.available)
    await wait_until(lambda: link.opens >= 2)
    charger.muted = False
    await wait_until(lambda: device.state.available)
    assert device.last_error is None


async def test_lost_link_reconnects(device: A2687Device,
                                    link: SimulatedLink) -> None:
    await connected(device)
    link.fail_next_open(ChargerConnectionError('out of range'))
    link.drop()
    await wait_until(lambda: not device.state.available)
    await wait_until(lambda: link.opens == 2)
    await wait_until(lambda: device.state.available)


async def test_first_connect_failure_is_raised(device: A2687Device,
                                               link: SimulatedLink) -> None:
    link.fail_next_open(ChargerConnectionError('not found'))
    with pytest.raises(ChargerConnectionError):
        await device.connect()
    await connected(device)


async def test_refused_identity_without_data_stops(
        link: SimulatedLink, charger: SimulatedCharger) -> None:
    charger.required_identity = 'owner-1'
    device = make_device(link)
    try:
        await device.connect()
        await wait_until(lambda: device.last_error is not None)
        assert isinstance(device.last_error, AuthenticationError)
        assert not device.state.available
        opens = link.opens
        await asyncio.sleep(0.1)
        assert link.opens == opens
        assert OP_USER_AUTH in charger.opcodes()
    finally:
        await device.disconnect()


async def test_disconnect_closes_and_stops(device: A2687Device,
                                           link: SimulatedLink) -> None:
    await connected(device)
    await device.disconnect()
    assert not link.is_open
    assert not device.state.available
    await asyncio.sleep(0.05)
    assert link.opens == 1


async def test_output_write_reads_back(device: A2687Device,
                                       charger: SimulatedCharger) -> None:
    await connected(device)
    await device.set_output(OutputId.C3, True)
    assert charger.ports[2].on
    assert device.state.outputs[OutputId.C3].is_on
    await device.set_output(OutputId.C1, False)
    assert not device.state.outputs[OutputId.C1].is_on


async def test_refused_writes_raise(device: A2687Device,
                                    charger: SimulatedCharger) -> None:
    await connected(device)
    charger.port_refusal = 3
    with pytest.raises(WriteRejectedError) as caught:
        await device.set_output(OutputId.C3, True)
    assert caught.value.status == 3
    charger.refused_writes[OP_BRIGHTNESS] = 0x11
    with pytest.raises(WriteRejectedError):
        await device.set_brightness(50)


async def test_unanswered_write_times_out(device: A2687Device,
                                          charger: SimulatedCharger) -> None:
    await connected(device)
    charger.silent.add(OP_CHARGING_MODE)
    with pytest.raises(ChargerTimeoutError):
        await device.set_charging_mode(ChargingMode.STANDARD)


async def test_setting_writes(device: A2687Device,
                              charger: SimulatedCharger) -> None:
    await connected(device)
    await device.set_charging_mode(ChargingMode.STANDARD)
    await device.set_brightness(40)
    await device.set_screen_timeout(ScreenTimeout.TWELVE_HOURS)
    await device.set_screen_orientation(ScreenOrientation.DOWN)
    await device.set_auto_rotation(True)
    await device.set_display_language(DisplayLanguage.GERMAN)
    await device.set_fixed_allocation(FixedAllocation.DUAL_LAPTOP)
    assert (charger.language, charger.allocation) == (3, 1)
    settings = device.state.settings
    assert settings.charging_mode == ChargingMode.STANDARD
    assert settings.fixed_allocation == FixedAllocation.DUAL_LAPTOP
    display = settings.display
    assert display.brightness == 40
    assert display.screen_timeout == ScreenTimeout.TWELVE_HOURS
    assert display.orientation == ScreenOrientation.DOWN
    assert display.auto_rotation is True
    assert display.language == DisplayLanguage.GERMAN


async def test_countdown(device: A2687Device,
                         charger: SimulatedCharger) -> None:
    await connected(device)
    await device.start_countdown(OutputId.C2, 600)
    assert charger.ports[1].countdown == 600
    countdown = device.state.outputs[OutputId.C2].countdown
    assert countdown is not None and countdown.total == 600
    await device.cancel_countdown(OutputId.C2)
    assert charger.ports[1].countdown == 0
    assert charger.opcodes().count(OP_PORT_TIMER) == 2


async def test_invalid_and_unsupported(device: A2687Device) -> None:
    await connected(device)
    with pytest.raises(InvalidParameterError):
        await device.set_brightness(10)
    with pytest.raises(InvalidParameterError):
        await device.start_countdown(OutputId.C1, 0)
    with pytest.raises(InvalidParameterError):
        await device.set_output(OutputId.C4, True)
    with pytest.raises(InvalidParameterError):
        await device.set_charging_mode(ChargingMode.CUSTOM)
    with pytest.raises(InvalidParameterError):
        await device.set_screen_timeout(ScreenTimeout.ALWAYS_ON)
    with pytest.raises(UnsupportedCapabilityError):
        await device.set_ufcs(True)
    # Custom mode takes limits; stored profiles are a 250W feature.
    assert device.supports(Capability.CUSTOM_CHARGING_MODE)
    assert not device.supports(Capability.CUSTOM_PROFILES)


async def test_writes_need_a_connection(device: A2687Device) -> None:
    with pytest.raises(ChargerConnectionError):
        await device.set_output(OutputId.C1, True)
    with pytest.raises(ChargerConnectionError):
        await device.refresh()


async def test_refresh_reads_everything(device: A2687Device,
                                        charger: SimulatedCharger) -> None:
    await connected(device)
    charger.requests.clear()
    await device.refresh()
    assert OP_READ_ALL in charger.opcodes()


async def test_on_demand_reads_periodically_and_disconnects(
        link: SimulatedLink, charger: SimulatedCharger) -> None:
    device = make_device(link, keep_connection=False)
    try:
        await device.connect()
        assert device.state.available
        assert device.state.ports[PortId.C1].power == pytest.approx(65.0)
        await wait_until(lambda: not link.is_open)
        await wait_until(lambda: link.opens >= 2, timeout=1)
        await wait_until(lambda: not link.is_open)
        assert device.state.available
    finally:
        await device.disconnect()


async def test_on_demand_commands_open_the_link(
        link: SimulatedLink, charger: SimulatedCharger) -> None:
    device = make_device(link, keep_connection=False)
    try:
        await device.connect()
        await wait_until(lambda: not link.is_open)
        opens = link.opens
        await device.set_output(OutputId.C3, True)
        await device.refresh()
        assert link.opens == opens + 1
        assert device.state.outputs[OutputId.C3].is_on
        await wait_until(lambda: not link.is_open)
    finally:
        await device.disconnect()


async def test_on_demand_first_read_failure_is_raised(
        link: SimulatedLink, charger: SimulatedCharger) -> None:
    charger.silent.update({OP_READ_ALL, OP_REALTIME_TRIGGER})
    device = make_device(link, keep_connection=False)
    try:
        with pytest.raises(ChargerTimeoutError):
            await device.connect()
    finally:
        await device.disconnect()


async def test_energy_is_kept_under_the_negotiated_serial(
        link: SimulatedLink) -> None:
    memory = MemoryStore({'energy-ashdmock00000000': {
        'version': 1, 'counters': {'c3': 2.5}}})
    device = make_device(link, energy_store=memory)
    await connected(device)
    assert device.state.energy[PortId.C3] == 2.5
    await wait_until(lambda: device.state.energy[PortId.C1] > 0)
    await device.reset_energy([PortId.C2])
    assert device.state.energy[PortId.C2] == 0
    with pytest.raises(InvalidParameterError):
        await device.reset_energy([PortId.C4])
    await device.disconnect()
    assert 'energy-ashdmock00000000' in memory.documents
