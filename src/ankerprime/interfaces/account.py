"""Charger settings kept in the user's account.

The device layer reads and writes them through this interface, so it does
not depend on the cloud client. ``CloudAccount`` provides one per charger;
tests use an in-memory fake.
"""

from abc import ABC, abstractmethod

from ankerprime.model.custom import (
    AccountSettings, ChargingProfile, CustomModeRules)
from ankerprime.model.pictures import ClockPicture


class ChargerAccount(ABC):
    """Account-side settings, custom profiles and pictures of one charger.

    Calls raise ``AuthenticationError`` when the account needs new
    credentials, ``ChargerConnectionError`` when the server is unreachable
    and ``ProtocolError`` when it refuses the request.
    """

    @property
    @abstractmethod
    def mainland(self) -> bool:
        """The account belongs to mainland China; limits and protocols differ."""

        pass

    @abstractmethod
    async def settings(self) -> AccountSettings:
        pass

    @abstractmethod
    async def set_custom_mode_enabled(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_max_compatibility(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def set_device_identification(self, enabled: bool) -> None:
        pass

    @abstractmethod
    async def profiles(self) -> list[ChargingProfile]:
        """Stored custom profiles, by number, with their account ids."""

        pass

    @abstractmethod
    async def add_profile(self, profile: ChargingProfile,
                          rules: CustomModeRules) -> None:
        pass

    @abstractmethod
    async def update_profile(self, profile: ChargingProfile,
                             rules: CustomModeRules) -> None:
        """Replace the stored profile with ``profile.profile_id``."""

        pass

    @abstractmethod
    async def delete_profile(self, profile_id: int) -> None:
        pass

    @abstractmethod
    async def clock_pictures(self, language: str = 'en'
                             ) -> list[ClockPicture]:
        """The catalogue of clock pictures; ``language`` ``en`` or ``zh``."""

        pass

    @abstractmethod
    async def own_pictures(self) -> list[ClockPicture]:
        """The user's own pictures, with freshly signed links."""

        pass

    @abstractmethod
    async def upload_own_picture(self, jpeg: bytes) -> None:
        """Store a prepared JPEG as an own picture."""

        pass

    @abstractmethod
    async def delete_own_picture(self, picture_id: int) -> None:
        pass
