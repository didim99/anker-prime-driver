"""High-level charger API, the only interface embedders need.

Every command raises ``UnsupportedCapabilityError`` when the charger lacks the
matching capability, ``InvalidParameterError`` for out-of-range arguments, and
``WriteRejectedError`` or ``ChargerTimeoutError`` when the charger refuses or
does not acknowledge the write.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection

from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, DisplayLanguage,
    FixedAllocation, HourFormat, KnobDirection, OutputId,
    PortId, ScheduleSlot, ScreenOrientation, ScreenTimeout)
from ankerprime.model.errors import AnkerPrimeError
from ankerprime.model.product import Product
from ankerprime.model.state import ChargerState, ClockSchedule, Schedule


ChargerListener = Callable[[ChargerState], None]
Unsubscribe = Callable[[], None]


class Charger(ABC):
    """One charger, regardless of how it is connected."""

    @property
    @abstractmethod
    def product(self) -> Product:
        pass

    @property
    @abstractmethod
    def state(self) -> ChargerState:
        """Latest snapshot; also available before the first connection."""

        pass

    @property
    @abstractmethod
    def last_error(self) -> AnkerPrimeError | None:
        """Error that stopped reconnecting; None while connecting or connected.

        An ``AuthenticationError`` here means new credentials are needed.
        """

        pass

    @abstractmethod
    def supports(self, capability: Capability) -> bool:
        """Check that the capability is usable on this unit."""

        pass

    @abstractmethod
    def subscribe(self, listener: ChargerListener) -> Unsubscribe:
        """Call ``listener`` with every new state; returns an unsubscriber."""

        pass

    @abstractmethod
    async def connect(self) -> None:
        """Connect and keep the connection alive until ``disconnect``.

        Raises when the first attempt fails; later losses are retried in the
        background with growing delays.
        """

        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def reset_energy(self, ports: Collection[PortId] | None = None
                           ) -> None:
        """Set the energy counters of the ports (all when None) to zero.

        Also works while disconnected. The reset is saved to the energy store
        at once, so other processes counting into the same store follow it.
        """

        pass

    @abstractmethod
    async def refresh(self) -> None:
        """Request a full state snapshot from the charger."""

        pass

    @abstractmethod
    async def set_output(self, output: OutputId, on: bool) -> None:
        pass

    @abstractmethod
    async def start_countdown(self, output: OutputId, seconds: int) -> None:
        """Switch the output off after the given number of seconds."""

        pass

    @abstractmethod
    async def cancel_countdown(self, output: OutputId) -> None:
        pass

    @abstractmethod
    async def set_schedule(self, output: OutputId, slot: ScheduleSlot,
                           schedule: Schedule) -> None:
        """Set one daily slot; a disabled schedule clears it."""

        pass

    @abstractmethod
    async def set_charging_mode(self, mode: ChargingMode) -> None:
        pass

    @abstractmethod
    async def set_preferred_outputs(self, outputs: Collection[OutputId]
                                    ) -> None:
        """Choose the preferred outputs of the port-priority mode."""

        pass

    @abstractmethod
    async def set_fixed_allocation(self, allocation: FixedAllocation) -> None:
        pass

    @abstractmethod
    async def set_brightness(self, percent: int) -> None:
        pass

    @abstractmethod
    async def set_screen_timeout(self, timeout: ScreenTimeout) -> None:
        pass

    @abstractmethod
    async def set_screen_orientation(self, orientation: ScreenOrientation
                                     ) -> None:
        pass

    @abstractmethod
    async def set_auto_rotation(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_display_language(self, language: DisplayLanguage) -> None:
        pass

    @abstractmethod
    async def set_knob_direction(self, direction: KnobDirection) -> None:
        pass

    @abstractmethod
    async def set_hour_format(self, hour_format: HourFormat) -> None:
        pass

    @abstractmethod
    async def set_clock(self, enabled: bool, theme: ClockTheme | None = None
                        ) -> None:
        """Switch the clock screensaver; ``theme`` picks a built-in style."""

        pass

    @abstractmethod
    async def set_clock_schedule(self, schedule: ClockSchedule) -> None:
        pass

    @abstractmethod
    async def set_holiday_theme(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_max_compatibility(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_device_identification(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_ufcs(self, enabled: bool) -> None:
        pass
