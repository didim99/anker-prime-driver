"""Charger state tests."""

from datetime import UTC, datetime

import pytest

from ankerprime.model import (
    A2345, Capability, ChargerState, ClockSchedule,
    Countdown, InvalidParameterError, OutputId, OutputState,
    PortId, PortReading, Schedule, Verification, Weekday)


NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def make_reading(port: PortId, status: int, voltage: float,
                 current: float) -> PortReading:
    return PortReading(port, status_code=status, voltage=voltage,
                       current=current, power=voltage * current)


@pytest.mark.parametrize(('hour', 'minute'), [(24, 0), (0, 60), (-1, 0)])
def test_schedule_rejects_invalid_time(hour: int, minute: int) -> None:
    with pytest.raises(InvalidParameterError):
        Schedule(True, hour, minute)
    with pytest.raises(InvalidParameterError):
        ClockSchedule(hour, minute, 0, 0)


def test_schedule_defaults_to_once() -> None:
    schedule = Schedule(True, 7, 30)
    assert schedule.weekdays == Weekday.ONCE
    assert ClockSchedule(22, 0, 7, 0).weekdays == Weekday.EVERY_DAY


def test_countdown_end_time() -> None:
    countdown = Countdown(total=3600, remaining=90, reported_at=NOW)
    assert countdown.ends_at == datetime(2026, 11, 4, 12, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize(('status', 'voltage', 'current', 'delivering'), [
    (1, 20.0, 2.5, True),
    (1, 5.0, 0.01, False),
    (1, 0.0, 1.0, False),
    (0, 20.0, 2.5, False),
])
def test_port_delivering(
        status: int, voltage: float, current: float, delivering: bool) -> None:
    reading = make_reading(PortId.C1, status, voltage, current)
    assert reading.active is (status != 0)
    assert reading.delivering is delivering


def test_total_power_counts_active_ports_only() -> None:
    state = ChargerState(A2345, ports={
        PortId.C1: make_reading(PortId.C1, 1, 20.0, 3.0),
        PortId.C2: make_reading(PortId.C2, 0, 5.0, 1.0),
        PortId.A1: make_reading(PortId.A1, 1, 5.0, 1.0),
    })
    assert state.total_power == pytest.approx(65.0)


def test_total_energy() -> None:
    state = ChargerState(A2345, energy={PortId.C1: 1.5, PortId.C2: 2.25})
    assert state.total_energy == pytest.approx(3.75)


def test_state_defaults() -> None:
    state = ChargerState(A2345)
    assert not state.available
    assert state.capabilities == A2345.capabilities
    assert state.settings.display.brightness is None
    assert state.info.serial is None
    assert not state.ports and not state.outputs


def test_capabilities_narrowed_by_firmware() -> None:
    narrowed = dict(A2345.capabilities)
    del narrowed[Capability.UFCS]
    state = ChargerState(A2345, capabilities=narrowed)
    assert not state.supports(Capability.UFCS)
    assert state.supports(Capability.OUTPUT_SWITCH)
    assert not state.supports(Capability.CUSTOM_CHARGING_MODE)
    assert state.capabilities[Capability.OUTPUT_SWITCH] == Verification.VERIFIED


def test_state_mappings_are_read_only_copies() -> None:
    outputs = {OutputId.C1: OutputState(OutputId.C1, is_on=True)}
    state = ChargerState(A2345, outputs=outputs)
    outputs[OutputId.C2] = OutputState(OutputId.C2)
    assert list(state.outputs) == [OutputId.C1]
    with pytest.raises(TypeError):
        state.outputs[OutputId.C2] = OutputState(OutputId.C2)  # type: ignore[index]


def test_value_equality() -> None:
    monday = Schedule(True, 7, 0, Weekday.MONDAY)
    assert monday == Schedule(True, 7, 0, Weekday.MONDAY)
    assert Schedule(True, 7, 0) != Schedule(False, 7, 0)
    assert ChargerState(A2345) == ChargerState(A2345)
