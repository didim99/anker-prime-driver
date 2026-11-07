"""Charger settings kept in the user's account.

The device layer reads and writes them through this interface, so it does
not depend on the cloud client. ``CloudAccount`` provides one per charger;
tests use an in-memory fake.
"""

from abc import ABC, abstractmethod

from ankerprime.model.custom import (
    AccountSettings, ChargingProfile, CustomModeRules)


class ChargerAccount(ABC):
    """Account-side settings and custom profiles of one charger.

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
