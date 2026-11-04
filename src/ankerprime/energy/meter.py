"""Cumulative per-port energy from a stream of power samples.

The chargers report power only, so energy is integrated locally from the
telemetry stream, at its full rate. Like Charker, nothing is guessed across
gaps: after a long silence integration starts over from the next sample.
"""

import math
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from ankerprime.model.enums import PortId


SECONDS_PER_HOUR = 3600.0
MAX_GAP = 120.0


class EnergyMeter:
    """Integrates power samples into watt-hour counters.

    Counters only grow, except through ``replace``.

    Consecutive samples are joined with the trapezoidal rule. A sample more
    than ``max_gap`` seconds after the previous one, or not after it at all,
    adds nothing and becomes the new starting point. Sample times are
    seconds on any monotonic clock.
    """

    _max_gap: float
    _counters: dict[PortId, float]
    _last_at: float | None
    _last_powers: dict[PortId, float]

    def __init__(self, ports: Iterable[PortId],
                 counters: Mapping[PortId, float] | None = None, *,
                 max_gap: float = MAX_GAP):
        self._max_gap = max_gap
        self._counters = dict.fromkeys(ports, 0.0)
        self._last_at = None
        self._last_powers = {}
        if counters is not None:
            self.restore(counters)

    @property
    def counters(self) -> Mapping[PortId, float]:
        """Energy per port in watt-hours (a read-only copy)."""

        return MappingProxyType(dict(self._counters))

    @property
    def total(self) -> float:
        return sum(self._counters.values())

    def record(self, at: float, powers: Mapping[PortId, float]) -> bool:
        """Add one sample of per-port watts; return whether energy grew.

        Ports missing from the sample count as drawing nothing, ports the
        meter does not know are ignored.
        """

        clean = {port: _non_negative(power) for port, power in powers.items()
                 if port in self._counters}
        previous_at = self._last_at
        previous = self._last_powers
        self._last_at = at
        self._last_powers = clean
        if previous_at is None:
            return False
        elapsed = at - previous_at
        if elapsed <= 0 or elapsed > self._max_gap:
            return False
        grew = False
        for port in clean.keys() | previous.keys():
            mean = (previous.get(port, 0.0) + clean.get(port, 0.0)) / 2
            energy = mean * elapsed / SECONDS_PER_HOUR
            if energy > 0:
                self._counters[port] += energy
                grew = True
        return grew

    def interrupt(self) -> None:
        """Forget the last sample, so the next one starts a new stretch."""

        self._last_at = None
        self._last_powers = {}

    def restore(self, counters: Mapping[PortId, float]) -> None:
        """Continue from stored counters; values never go down."""

        for port, value in counters.items():
            if port in self._counters:
                self._counters[port] = max(self._counters[port],
                                           _non_negative(value))

    def replace(self, counters: Mapping[PortId, float]) -> None:
        """Overwrite counters, also downwards (resets)."""

        for port, value in counters.items():
            if port in self._counters:
                self._counters[port] = _non_negative(value)


def _non_negative(value: float) -> float:
    return value if math.isfinite(value) and value > 0 else 0.0
