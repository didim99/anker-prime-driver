"""Shared ``Charger`` behaviour: listeners and capability gating.

Every public command checks its capability first, then calls a protected
hook. Hooks a product does not implement raise ``UnsupportedCapabilityError``.
"""

import logging
from collections.abc import Collection

from ankerprime.interfaces.charger import Charger, ChargerListener, Unsubscribe
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, DisplayLanguage, FixedAllocation, HourFormat,
    KnobDirection, OutputId, ScheduleSlot, ScreenOrientation, ScreenTimeout)
from ankerprime.model.errors import AnkerPrimeError, UnsupportedCapabilityError
from ankerprime.model.product import Product
from ankerprime.model.state import ChargerState, ClockSchedule, Schedule


logger = logging.getLogger(__name__)


class BaseCharger(Charger):
    """Holds the published state and the listeners."""

    _product: Product

    _state: ChargerState
    _last_error: AnkerPrimeError | None

    _listeners: list[ChargerListener]

    def __init__(self, product: Product):
        self._product = product
        self._state = ChargerState(product)
        self._last_error = None
        self._listeners = []

    @property
    def product(self) -> Product:
        return self._product

    @property
    def state(self) -> ChargerState:
        return self._state

    @property
    def last_error(self) -> AnkerPrimeError | None:
        return self._last_error

    def supports(self, capability: Capability) -> bool:
        return self._state.supports(capability)

    def subscribe(self, listener: ChargerListener) -> Unsubscribe:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def set_output(self, output: OutputId, on: bool) -> None:
        self._require(Capability.OUTPUT_SWITCH)
        await self._set_output(output, on)

    async def start_countdown(self, output: OutputId, seconds: int) -> None:
        self._require(Capability.OUTPUT_COUNTDOWN)
        await self._start_countdown(output, seconds)

    async def cancel_countdown(self, output: OutputId) -> None:
        self._require(Capability.OUTPUT_COUNTDOWN)
        await self._cancel_countdown(output)

    async def set_schedule(self, output: OutputId, slot: ScheduleSlot,
                           schedule: Schedule) -> None:
        self._require(Capability.OUTPUT_SCHEDULE)
        await self._set_schedule(output, slot, schedule)

    async def set_charging_mode(self, mode: ChargingMode) -> None:
        self._require(Capability.CHARGING_MODE)
        await self._set_charging_mode(mode)

    async def set_preferred_outputs(self, outputs: Collection[OutputId]
                                    ) -> None:
        self._require(Capability.PORT_PRIORITY)
        await self._set_preferred_outputs(outputs)

    async def set_fixed_allocation(self, allocation: FixedAllocation) -> None:
        self._require(Capability.FIXED_ALLOCATION)
        await self._set_fixed_allocation(allocation)

    async def set_brightness(self, percent: int) -> None:
        self._require(Capability.SCREEN_BRIGHTNESS)
        await self._set_brightness(percent)

    async def set_screen_timeout(self, timeout: ScreenTimeout) -> None:
        self._require(Capability.SCREEN_TIMEOUT)
        await self._set_screen_timeout(timeout)

    async def set_screen_orientation(self, orientation: ScreenOrientation
                                     ) -> None:
        self._require(Capability.SCREEN_ORIENTATION)
        await self._set_screen_orientation(orientation)

    async def set_auto_rotation(self, enabled: bool) -> None:
        self._require(Capability.AUTO_ROTATION)
        await self._set_auto_rotation(enabled)

    async def set_display_language(self, language: DisplayLanguage) -> None:
        self._require(Capability.DISPLAY_LANGUAGE)
        await self._set_display_language(language)

    async def set_knob_direction(self, direction: KnobDirection) -> None:
        self._require(Capability.KNOB_DIRECTION)
        await self._set_knob_direction(direction)

    async def set_hour_format(self, hour_format: HourFormat) -> None:
        self._require(Capability.HOUR_FORMAT)
        await self._set_hour_format(hour_format)

    async def set_clock(self, enabled: bool, theme: ClockTheme | None = None
                        ) -> None:
        self._require(Capability.CLOCK_SCREENSAVER)
        await self._set_clock(enabled, theme)

    async def set_clock_schedule(self, schedule: ClockSchedule) -> None:
        self._require(Capability.CLOCK_SCHEDULE)
        await self._set_clock_schedule(schedule)

    async def set_holiday_theme(self, enabled: bool) -> None:
        self._require(Capability.HOLIDAY_THEME)
        await self._set_holiday_theme(enabled)

    async def set_max_compatibility(self, enabled: bool) -> None:
        self._require(Capability.MAX_COMPATIBILITY)
        await self._set_max_compatibility(enabled)

    async def set_device_identification(self, enabled: bool) -> None:
        self._require(Capability.DEVICE_IDENTIFICATION)
        await self._set_device_identification(enabled)

    async def set_ufcs(self, enabled: bool) -> None:
        self._require(Capability.UFCS)
        await self._set_ufcs(enabled)

    def _require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise UnsupportedCapabilityError(capability)

    def _publish(self, state: ChargerState) -> None:
        """Store a new state and notify listeners when it changed."""

        if state == self._state:
            return
        self._state = state
        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception:
                logger.exception('charger state listener failed')

    async def _set_output(self, output: OutputId, on: bool) -> None:
        raise UnsupportedCapabilityError(Capability.OUTPUT_SWITCH)

    async def _start_countdown(self, output: OutputId, seconds: int) -> None:
        raise UnsupportedCapabilityError(Capability.OUTPUT_COUNTDOWN)

    async def _cancel_countdown(self, output: OutputId) -> None:
        raise UnsupportedCapabilityError(Capability.OUTPUT_COUNTDOWN)

    async def _set_schedule(self, output: OutputId, slot: ScheduleSlot,
                            schedule: Schedule) -> None:
        raise UnsupportedCapabilityError(Capability.OUTPUT_SCHEDULE)

    async def _set_charging_mode(self, mode: ChargingMode) -> None:
        raise UnsupportedCapabilityError(Capability.CHARGING_MODE)

    async def _set_preferred_outputs(self, outputs: Collection[OutputId]
                                     ) -> None:
        raise UnsupportedCapabilityError(Capability.PORT_PRIORITY)

    async def _set_fixed_allocation(self, allocation: FixedAllocation
                                    ) -> None:
        raise UnsupportedCapabilityError(Capability.FIXED_ALLOCATION)

    async def _set_brightness(self, percent: int) -> None:
        raise UnsupportedCapabilityError(Capability.SCREEN_BRIGHTNESS)

    async def _set_screen_timeout(self, timeout: ScreenTimeout) -> None:
        raise UnsupportedCapabilityError(Capability.SCREEN_TIMEOUT)

    async def _set_screen_orientation(self, orientation: ScreenOrientation
                                      ) -> None:
        raise UnsupportedCapabilityError(Capability.SCREEN_ORIENTATION)

    async def _set_auto_rotation(self, enabled: bool) -> None:
        raise UnsupportedCapabilityError(Capability.AUTO_ROTATION)

    async def _set_display_language(self, language: DisplayLanguage) -> None:
        raise UnsupportedCapabilityError(Capability.DISPLAY_LANGUAGE)

    async def _set_knob_direction(self, direction: KnobDirection) -> None:
        raise UnsupportedCapabilityError(Capability.KNOB_DIRECTION)

    async def _set_hour_format(self, hour_format: HourFormat) -> None:
        raise UnsupportedCapabilityError(Capability.HOUR_FORMAT)

    async def _set_clock(self, enabled: bool, theme: ClockTheme | None
                         ) -> None:
        raise UnsupportedCapabilityError(Capability.CLOCK_SCREENSAVER)

    async def _set_clock_schedule(self, schedule: ClockSchedule) -> None:
        raise UnsupportedCapabilityError(Capability.CLOCK_SCHEDULE)

    async def _set_holiday_theme(self, enabled: bool) -> None:
        raise UnsupportedCapabilityError(Capability.HOLIDAY_THEME)

    async def _set_max_compatibility(self, enabled: bool) -> None:
        raise UnsupportedCapabilityError(Capability.MAX_COMPATIBILITY)

    async def _set_device_identification(self, enabled: bool) -> None:
        raise UnsupportedCapabilityError(Capability.DEVICE_IDENTIFICATION)

    async def _set_ufcs(self, enabled: bool) -> None:
        raise UnsupportedCapabilityError(Capability.UFCS)
