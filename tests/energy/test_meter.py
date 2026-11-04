"""Energy meter integration tests."""

import math

import pytest

from ankerprime.energy.meter import EnergyMeter
from ankerprime.model import PortId


PORTS = (PortId.C1, PortId.C2)


def test_counters_start_at_zero_for_every_port() -> None:
    meter = EnergyMeter(PORTS)
    assert dict(meter.counters) == {PortId.C1: 0.0, PortId.C2: 0.0}
    assert meter.total == 0.0


def test_first_sample_only_sets_the_starting_point() -> None:
    meter = EnergyMeter(PORTS)
    assert not meter.record(0.0, {PortId.C1: 100.0})
    assert meter.total == 0.0


def test_samples_are_joined_with_the_trapezoidal_rule() -> None:
    meter = EnergyMeter(PORTS)
    meter.record(0.0, {PortId.C1: 0.0, PortId.C2: 36.0})
    assert meter.record(10.0, {PortId.C1: 72.0, PortId.C2: 36.0})
    assert meter.counters[PortId.C1] == pytest.approx(0.1)
    assert meter.counters[PortId.C2] == pytest.approx(0.1)
    meter.record(20.0, {PortId.C1: 72.0})
    assert meter.counters[PortId.C1] == pytest.approx(0.3)
    assert meter.counters[PortId.C2] == pytest.approx(0.15)
    assert meter.total == pytest.approx(0.45)


def test_nothing_is_guessed_across_a_gap() -> None:
    meter = EnergyMeter(PORTS, max_gap=60.0)
    meter.record(0.0, {PortId.C1: 3600.0})
    assert not meter.record(61.0, {PortId.C1: 3600.0})
    assert meter.total == 0.0
    meter.record(62.0, {PortId.C1: 3600.0})
    assert meter.counters[PortId.C1] == pytest.approx(1.0)


@pytest.mark.parametrize('second_at', [10.0, 9.0])
def test_repeated_or_earlier_time_restarts(second_at: float) -> None:
    meter = EnergyMeter(PORTS)
    meter.record(10.0, {PortId.C1: 3600.0})
    assert not meter.record(second_at, {PortId.C1: 3600.0})
    meter.record(second_at + 1, {PortId.C1: 3600.0})
    assert meter.counters[PortId.C1] == pytest.approx(1.0)


@pytest.mark.parametrize('power', [-5.0, math.nan, math.inf])
def test_unusable_power_counts_as_zero(power: float) -> None:
    meter = EnergyMeter(PORTS)
    meter.record(0.0, {PortId.C1: power})
    assert not meter.record(1.0, {PortId.C1: power})
    assert meter.total == 0.0


def test_unknown_ports_are_ignored() -> None:
    meter = EnergyMeter(PORTS)
    meter.record(0.0, {PortId.A1: 100.0})
    meter.record(1.0, {PortId.A1: 100.0})
    assert PortId.A1 not in meter.counters and meter.total == 0.0


def test_interrupt_starts_a_new_stretch() -> None:
    meter = EnergyMeter(PORTS)
    meter.record(0.0, {PortId.C1: 3600.0})
    meter.interrupt()
    assert not meter.record(1.0, {PortId.C1: 3600.0})
    assert meter.total == 0.0


def test_restore_continues_and_never_lowers() -> None:
    meter = EnergyMeter(PORTS, {PortId.C1: 5.0, PortId.A1: 9.0})
    assert dict(meter.counters) == {PortId.C1: 5.0, PortId.C2: 0.0}
    meter.restore({PortId.C1: 2.0, PortId.C2: -1.0})
    assert dict(meter.counters) == {PortId.C1: 5.0, PortId.C2: 0.0}
    meter.record(0.0, {PortId.C1: 3600.0})
    meter.record(1.0, {PortId.C1: 3600.0})
    assert meter.counters[PortId.C1] == pytest.approx(6.0)


def test_counters_are_a_copy() -> None:
    meter = EnergyMeter(PORTS)
    counters = meter.counters
    meter.record(0.0, {PortId.C1: 3600.0})
    meter.record(1.0, {PortId.C1: 3600.0})
    assert counters[PortId.C1] == 0.0
