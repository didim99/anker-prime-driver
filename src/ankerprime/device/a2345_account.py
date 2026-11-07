"""A2345 custom mode and the switches the Anker account keeps.

Used by ``A2345Device``: custom profiles (stored in the account, applied on
the charger), the account's custom mode setting, and the max compatibility
and device identification switches, which exclude each other and go to the
account first, then to the charger, as the official app does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from ankerprime.interfaces.account import ChargerAccount
from ankerprime.model.account import AccountSettings
from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import ChargingProfile, CustomModeRules
from ankerprime.model.errors import (
    AnkerPrimeError, InvalidParameterError, UnsupportedCapabilityError)
from ankerprime.protocol.a2345 import commands
from ankerprime.protocol.a2345.commands import Command
from ankerprime.protocol.a2345.device_settings import DeviceSettings
from ankerprime.protocol.a2345.settings import (
    CUSTOM_CHARGING_MODE_CODE, Capabilities, CustomProfile, SettingField)
from ankerprime.protocol.a2345.updates import (
    ChargingModeUpdate, SettingsUpdate, ValueUpdate)


logger = logging.getLogger(__name__)


class CommandWriter(Protocol):
    """``A2345Device._write``: send, wait for the ack, apply the change."""

    async def __call__(self, command: Command,
                       expected: Sequence[SettingsUpdate] = (), *,
                       waits: Sequence[float] | None = None,
                       include_theme: bool = False) -> None:
        pass


SettingsSource = Callable[[], DeviceSettings | None]


class AccountFeatures:
    """Custom profiles, the custom mode setting and the exclusive switches."""

    _account: ChargerAccount | None
    _rules: CustomModeRules | None
    _write: CommandWriter
    _device_settings: SettingsSource
    _publish: Callable[[], None]
    _label: Callable[[], str]
    _custom_waits: Sequence[float]

    _settings: AccountSettings | None
    _profiles: tuple[ChargingProfile, ...] | None
    _lock: asyncio.Lock

    def __init__(self, account: ChargerAccount | None,
                 rules: CustomModeRules | None, *, write: CommandWriter,
                 device_settings: SettingsSource,
                 publish: Callable[[], None], label: Callable[[], str],
                 custom_waits: Sequence[float]):
        self._account = account
        self._rules = rules
        self._write = write
        self._device_settings = device_settings
        self._publish = publish
        self._label = label
        self._custom_waits = custom_waits
        self._settings = None
        self._profiles = None
        self._lock = asyncio.Lock()

    @property
    def account(self) -> ChargerAccount | None:
        return self._account

    @property
    def settings(self) -> AccountSettings | None:
        """What the account reported, None until read."""

        return self._settings

    @property
    def profiles(self) -> tuple[ChargingProfile, ...] | None:
        return self._profiles

    def require(self, capability: Capability = Capability.CUSTOM_PROFILES
                ) -> ChargerAccount:
        if self._account is None:
            raise UnsupportedCapabilityError(capability)
        return self._account

    async def apply_profile(self, profile: ChargingProfile) -> None:
        mainland = self._account is not None and self._account.mainland
        self._custom_rules().validate(profile, mainland)
        settings = self._device_settings()
        with_protocols = (profile.protocols_enabled and settings is not None
                          and settings.supports(Capabilities.PORT_PROTOCOLS)
                          and not settings.supports(
                              Capabilities.PROTOCOL_MANAGEMENT))
        command = commands.apply_custom_profile(profile, mainland=mainland,
                                                with_protocols=with_protocols)
        reported = CustomProfile(profile.number, profile.auto_exit,
                                 command.fields[0xA3][2:])
        update = ChargingModeUpdate(CUSTOM_CHARGING_MODE_CODE, reported)
        await self._write(command, [update], waits=self._custom_waits)

    async def exit_mode(self) -> None:
        await self._write(commands.exit_custom_charging_mode())

    async def set_enabled(self, enabled: bool) -> None:
        """The account's Labs switch; switching it off leaves custom mode."""

        account = self.require()
        await account.set_custom_mode_enabled(enabled)
        self._update(custom_mode_enabled=enabled)
        settings = self._device_settings()
        if not enabled and settings is not None and (
                settings.custom_charging_mode):
            await self.exit_mode()

    async def save(self, profile: ChargingProfile) -> None:
        account = self.require()
        mainland = account.mainland
        rules = self._custom_rules()
        profile = rules.normalize(profile, mainland)
        rules.validate(profile, mainland)
        stored = await self._stored(profile.number)
        if stored is None:
            await account.add_profile(profile, rules)
        else:
            profile = profile.replace(profile_id=stored.profile_id)
            await account.update_profile(profile, rules)
        await self.reload()
        if self._active() == profile.number:
            await self.apply_profile(profile)

    async def delete(self, number: int) -> None:
        account = self.require()
        stored = await self._stored(number)
        if stored is None or stored.profile_id is None:
            raise InvalidParameterError(f'no stored custom profile {number}')
        if self._active() == number:
            # Only removed from the account once the charger has left it.
            await self.exit_mode()
        await account.delete_profile(stored.profile_id)
        await self.reload()

    async def reload(self) -> None:
        account = self.require()
        async with self._lock:
            settings = await account.settings()
            profiles = await account.profiles()
        self._settings = settings
        self._profiles = tuple(profiles)
        self._publish()

    async def load(self) -> None:
        """Read the account's values; a failure leaves the previous ones."""

        try:
            await self.reload()
        except AnkerPrimeError as error:
            logger.debug('account settings of %s not read: %s',
                         self._label(), error)

    async def set_max_compatibility(self, enabled: bool) -> None:
        # Max compatibility and device identification exclude each other.
        if enabled and self._switched_on(SettingField.DEVICE_IDENTIFICATION):
            await self._store_switch(SettingField.DEVICE_IDENTIFICATION,
                                     False)
        await self._store_switch(SettingField.MAX_COMPATIBILITY, enabled)

    async def set_device_identification(self, enabled: bool) -> None:
        if enabled and self._switched_on(SettingField.MAX_COMPATIBILITY):
            await self._store_switch(SettingField.MAX_COMPATIBILITY, False)
        await self._store_switch(SettingField.DEVICE_IDENTIFICATION, enabled)

    def _custom_rules(self) -> CustomModeRules:
        if self._rules is None:
            raise UnsupportedCapabilityError(Capability.CUSTOM_CHARGING_MODE)
        return self._rules

    def _active(self) -> int | None:
        """Number of the custom profile in use."""

        settings = self._device_settings()
        if settings is None or not settings.custom_charging_mode:
            return None
        profile = settings.custom_profile
        return profile.number if profile is not None else None

    async def _stored(self, number: int) -> ChargingProfile | None:
        if self._profiles is None:
            await self.reload()
        for profile in self._profiles or ():
            if profile.number == number:
                return profile
        return None

    def _update(self, **changes: bool) -> None:
        current = self._settings or AccountSettings()
        self._settings = current.replace(**changes)
        self._publish()

    def _switched_on(self, field: SettingField) -> bool:
        """Account value first, then what the charger reports."""

        compatibility = field == SettingField.MAX_COMPATIBILITY
        stored = reported = None
        if self._settings is not None:
            stored = (self._settings.max_compatibility if compatibility
                      else self._settings.device_identification)
        settings = self._device_settings()
        if settings is not None:
            reported = (settings.max_compatibility if compatibility
                        else settings.device_identification)
        return bool(stored if stored is not None else reported)

    async def _store_switch(self, field: SettingField, enabled: bool) -> None:
        compatibility = field == SettingField.MAX_COMPATIBILITY
        if self._account is not None:
            if compatibility:
                await self._account.set_max_compatibility(enabled)
                self._update(max_compatibility=enabled)
            else:
                await self._account.set_device_identification(enabled)
                self._update(device_identification=enabled)
        command = (commands.set_max_compatibility(enabled) if compatibility
                   else commands.set_device_identification(enabled))
        await self._write(command, [ValueUpdate(field, int(enabled))])
