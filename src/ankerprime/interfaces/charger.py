"""High-level charger API, the only interface embedders need.

Every command raises ``UnsupportedCapabilityError`` when the charger lacks the
matching capability, ``InvalidParameterError`` for out-of-range arguments, and
``WriteRejectedError`` or ``ChargerTimeoutError`` when the charger refuses or
does not acknowledge the write.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection

from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import ChargingProfile
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, DisplayLanguage,
    FixedAllocation, HourFormat, KnobDirection, OutputId,
    PortId, ScheduleSlot, ScreenOrientation, ScreenTimeout)
from ankerprime.model.errors import AnkerPrimeError
from ankerprime.model.pictures import ClockPicture, ScreenPictureUpload
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

    @abstractmethod
    async def apply_custom_profile(self, profile: ChargingProfile) -> None:
        """Switch to custom mode with the profile's limits.

        The profile must satisfy ``product.custom_mode``; it need not be
        stored. Every port drops out for a few seconds while the charger
        renegotiates.
        """

        pass

    @abstractmethod
    async def exit_custom_mode(self) -> None:
        """Leave custom mode; the charger returns to its previous mode."""

        pass

    @abstractmethod
    async def set_custom_mode_enabled(self, enabled: bool) -> None:
        """Switch the account's Labs setting that offers custom mode.

        Switching it off also leaves custom mode, as the official app does.
        """

        pass

    @abstractmethod
    async def save_custom_profile(self, profile: ChargingProfile) -> None:
        """Store a profile in the account under its number.

        Replaces a stored profile with the same number; when that profile is
        in use, the charger gets the new limits too. The name is trimmed and,
        outside mainland China, SCP is dropped on ports capped at 15–20 W.
        """

        pass

    @abstractmethod
    async def delete_custom_profile(self, number: int) -> None:
        """Remove a stored profile, leaving custom mode first if it is in use."""

        pass

    @abstractmethod
    async def reload_custom_profiles(self) -> None:
        """Read the account's settings and profiles into the state again."""

        pass

    @abstractmethod
    async def clock_pictures(self, language: str = 'en'
                             ) -> tuple[ClockPicture, ...]:
        """The cloud catalogue of clock pictures; ``language`` ``en`` or ``zh``."""

        pass

    @abstractmethod
    async def own_clock_pictures(self) -> tuple[ClockPicture, ...]:
        """The account's own clock pictures (at most eight)."""

        pass

    @abstractmethod
    async def apply_clock_picture(self, picture: ClockPicture) -> None:
        """Show a picture behind the clock and switch the clock on.

        The charger downloads the picture itself; this returns once it
        reports the download finished and raises ``PictureTransferError``
        when it failed, ``ChargerTimeoutError`` without a report. An own
        picture is looked up again first for a freshly signed link.
        """

        pass

    @abstractmethod
    async def upload_clock_picture(self, jpeg: bytes) -> None:
        """Store a prepared JPEG (``ankerprime.pictures``) as an own picture."""

        pass

    @abstractmethod
    async def delete_clock_picture(self, picture_id: int) -> None:
        """Remove an own picture from the account."""

        pass

    @abstractmethod
    async def set_picture_time(self, enabled: bool) -> None:
        """Show the time over own pictures."""

        pass

    @abstractmethod
    async def upload_screen_picture(self, jpeg: bytes, *,
                                    acknowledge_irreversible: bool
                                    ) -> ScreenPictureUpload:
        """Write a prepared JPEG to the charger's screen (160W) and show it.

        **Cannot be undone**: the picture stays in the charger's storage, and
        no Bluetooth command erases it (the charger keeps four; a fifth
        evicts the oldest). ``acknowledge_irreversible`` must be True: the
        caller confirms the user was told so. Returns once the charger
        reports the picture shown; ``PictureTransferError.pixels_written``
        tells whether a failure left data behind.
        """

        pass

    @abstractmethod
    async def select_screen_picture(self, picture_id: int, crc32: int
                                    ) -> None:
        """Show a picture already stored on the charger (160W); reversible."""

        pass
