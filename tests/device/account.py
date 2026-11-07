"""In-memory ``ChargerAccount`` for device tests."""

import zlib

from ankerprime.interfaces.account import ChargerAccount
from ankerprime.model import (
    AccountSettings, ChargingProfile, ClockPicture, CustomModeRules)


class FakeAccount(ChargerAccount):
    """Account-side settings in memory; records every call."""

    _mainland: bool
    _settings: AccountSettings
    _profiles: dict[int, ChargingProfile]
    _next_id: int
    _calls: list[tuple[str, object]]
    _failure: Exception | None
    _catalogue: list[ClockPicture]
    _own: list[ClockPicture]

    def __init__(self, *, mainland: bool = False):
        self._mainland = mainland
        self._settings = AccountSettings(custom_mode_enabled=True,
                                         max_compatibility=False,
                                         device_identification=True)
        self._profiles = {}
        self._next_id = 10
        self._calls = []
        self._failure = None
        self._catalogue = []
        self._own = []

    @property
    def calls(self) -> list[tuple[str, object]]:
        return self._calls

    def fail_with(self, error: Exception | None) -> None:
        self._failure = error

    def store(self, profile: ChargingProfile) -> ChargingProfile:
        self._next_id += 1
        stored = profile.replace(profile_id=self._next_id)
        self._profiles[self._next_id] = stored
        return stored

    @property
    def mainland(self) -> bool:
        return self._mainland

    async def settings(self) -> AccountSettings:
        self._check()
        return self._settings

    async def set_custom_mode_enabled(self, enabled: bool) -> None:
        self._record('custom_mode', enabled)
        self._settings = self._settings.replace(custom_mode_enabled=enabled)

    async def set_max_compatibility(self, enabled: bool) -> None:
        self._record('max_compatibility', enabled)
        self._settings = self._settings.replace(max_compatibility=enabled)

    async def set_device_identification(self, enabled: bool) -> None:
        self._record('device_identification', enabled)
        self._settings = self._settings.replace(
            device_identification=enabled)

    async def profiles(self) -> list[ChargingProfile]:
        self._check()
        return sorted(self._profiles.values(),
                      key=lambda profile: profile.number)

    async def add_profile(self, profile: ChargingProfile,
                          rules: CustomModeRules) -> None:
        self._record('add', profile)
        self.store(profile)

    async def update_profile(self, profile: ChargingProfile,
                             rules: CustomModeRules) -> None:
        self._record('update', profile)
        assert profile.profile_id in self._profiles
        self._profiles[profile.profile_id] = profile

    async def delete_profile(self, profile_id: int) -> None:
        self._record('delete', profile_id)
        del self._profiles[profile_id]

    @property
    def catalogue(self) -> list[ClockPicture]:
        return self._catalogue

    @property
    def own(self) -> list[ClockPicture]:
        return self._own

    async def clock_pictures(self, language: str = 'en'
                             ) -> list[ClockPicture]:
        self._record('clock_pictures', language)
        return list(self._catalogue)

    async def own_pictures(self) -> list[ClockPicture]:
        self._record('own_pictures', None)
        return list(self._own)

    async def upload_own_picture(self, jpeg: bytes) -> None:
        self._record('upload', jpeg)
        picture_id = 900 + len(self._own)
        self._own.append(ClockPicture(picture_id, f'https://own/{picture_id}',
                                      zlib.crc32(jpeg), own=True))

    async def delete_own_picture(self, picture_id: int) -> None:
        self._record('delete_picture', picture_id)
        self._own = [picture for picture in self._own
                     if picture.picture_id != picture_id]

    def _record(self, name: str, value: object) -> None:
        self._check()
        self._calls.append((name, value))

    def _check(self) -> None:
        if self._failure is not None:
            raise self._failure
