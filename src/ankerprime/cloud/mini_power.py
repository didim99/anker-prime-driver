"""Charger settings the Anker account keeps (``mini_power`` API, 250W).

The account stores the Labs switch that makes custom mode available, the
max compatibility and device identification switches, and up to four
custom charging profiles. The official app writes each switch here before
sending it to the charger.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ankerprime.cloud.client import CloudClient
from ankerprime.model.custom import (
    AccountSettings, ChargingProfile,
    CustomModeRules, CustomPortLimit, CustomProtocol)
from ankerprime.model.enums import OutputId
from ankerprime.model.errors import ProtocolError


SETTINGS_PATH = 'mini_power/v1/app/setting/get_device_setting'
IDENTIFICATION_PATH = ('mini_power/v1/app/setting/'
                       'get_charging_device_identity_status_default_true')
SET_CUSTOM_MODE_PATH = 'mini_power/v1/app/setting/set_charging_mode_status'
SET_COMPATIBILITY_PATH = 'mini_power/v1/app/setting/set_compatibility_status'
SET_IDENTIFICATION_PATH = ('mini_power/v1/app/setting/'
                           'set_charging_device_identity_status_default_true')
PROFILES_PATH = 'mini_power/v1/app/charging/get_charging_mode_list'
ADD_PROFILE_PATH = 'mini_power/v1/app/charging/add_charging_mode'
UPDATE_PROFILE_PATH = 'mini_power/v1/app/charging/update_charging_mode'
DELETE_PROFILE_PATH = 'mini_power/v1/app/charging/delete_charging_mode'

CUSTOM_MODE_KEY = 'charging_mode_status'
COMPATIBILITY_KEY = 'compatibility_status'
IDENTIFICATION_KEY = 'charging_device_identity_status_default_true'

PORT_NAMES = {
    OutputId.C1: 'C1',
    OutputId.C2: 'C2',
    OutputId.C3: 'C3',
    OutputId.C4: 'C4',
    OutputId.USB_A: 'A',
}
PROTOCOL_KEYS = {
    CustomProtocol.SCP: 'scp',
    CustomProtocol.UFCS: 'ufcs',
    CustomProtocol.PD_12V: 'pd12v',
    CustomProtocol.PPS_11V: 'pps11v',
    CustomProtocol.PPS_16V: 'pps16v',
    CustomProtocol.PPS_21V: 'pps20v',
    CustomProtocol.HUAWEI: 'huawei',
    CustomProtocol.XIAOMI: 'xiaomi',
}


class MiniPowerClient:
    """Account-side settings and custom profiles of one 250W."""

    _client: CloudClient
    _serial: str

    MAINLAND_COUNTRY: ClassVar[str] = 'CN'

    def __init__(self, client: CloudClient, serial: str):
        self._client = client
        self._serial = serial

    @property
    def mainland(self) -> bool:
        """The account is served by the mainland China server."""

        country = self._client.authentication.country
        return country.upper() == self.MAINLAND_COUNTRY

    async def settings(self) -> AccountSettings:
        data = await self._client.request(SETTINGS_PATH, self._serial_body())
        values = data if isinstance(data, Mapping) else {}
        nested = values.get('device_setting')
        if isinstance(nested, Mapping):
            values = nested
        # The identity flag in that reply belongs to the 160W; the 250W keeps
        # its switch behind a separate endpoint.
        try:
            identification = await self._client.request(IDENTIFICATION_PATH,
                                                        self._serial_body())
        except ProtocolError:
            identification = None
        identified = None
        if isinstance(identification, Mapping):
            identified = flexible_bool(identification.get(IDENTIFICATION_KEY))
            if identified is None:
                identified = flexible_bool(identification.get('status'))
        return AccountSettings(
            custom_mode_enabled=flexible_bool(values.get(CUSTOM_MODE_KEY)),
            max_compatibility=flexible_bool(values.get(COMPATIBILITY_KEY)),
            device_identification=identified)

    async def set_custom_mode_enabled(self, enabled: bool) -> None:
        await self._switch(SET_CUSTOM_MODE_PATH, CUSTOM_MODE_KEY, enabled)

    async def set_max_compatibility(self, enabled: bool) -> None:
        await self._switch(SET_COMPATIBILITY_PATH, COMPATIBILITY_KEY, enabled)

    async def set_device_identification(self, enabled: bool) -> None:
        await self._switch(SET_IDENTIFICATION_PATH, IDENTIFICATION_KEY,
                           enabled)

    async def profiles(self) -> list[ChargingProfile]:
        """Stored custom profiles, by number."""

        data = await self._client.request(PROFILES_PATH, self._serial_body())
        items = data
        if isinstance(data, Mapping):
            items = data.get('charging_mode_list')
        if items is None:
            return []
        if not isinstance(items, list):
            raise ProtocolError('custom profile list missing from the response')
        profiles = [decode_profile(item) for item in items
                    if isinstance(item, Mapping)]
        return sorted((profile for profile in profiles if profile is not None),
                      key=lambda profile: profile.number)

    async def add_profile(self, profile: ChargingProfile,
                          rules: CustomModeRules) -> None:
        body = encode_profile(profile, rules, self.mainland)
        body['device_sn'] = self._serial
        body['number'] = profile.number
        await self._client.request(ADD_PROFILE_PATH, body)

    async def update_profile(self, profile: ChargingProfile,
                             rules: CustomModeRules) -> None:
        if profile.profile_id is None:
            raise ProtocolError('profile has no account id')
        body = encode_profile(profile, rules, self.mainland)
        body['id'] = profile.profile_id
        await self._client.request(UPDATE_PROFILE_PATH, body)

    async def delete_profile(self, profile_id: int) -> None:
        await self._client.request(DELETE_PROFILE_PATH, {'id': profile_id})

    async def _switch(self, path: str, key: str, enabled: bool) -> None:
        body = self._serial_body()
        body[key] = int(enabled)
        await self._client.request(path, body)

    def _serial_body(self) -> dict[str, Any]:
        return {'device_sn': self._serial}


def decode_profile(item: Mapping[str, Any]) -> ChargingProfile | None:
    """One profile of the account's list; None without a usable number."""

    number = flexible_int(item.get('number'))
    if number is None:
        return None
    settings = item.get('power_settings')
    ports = []
    if isinstance(settings, list):
        outputs = list(PORT_NAMES)
        for index, setting in enumerate(settings):
            if not isinstance(setting, Mapping):
                continue
            output = _output_named(setting.get('name'))
            if output is None and index < len(outputs):
                output = outputs[index]
            if output is None:
                continue
            protocols = [protocol for protocol, key in PROTOCOL_KEYS.items()
                         if flexible_bool(setting.get(key))]
            watts = flexible_int(setting.get('power')) or 0
            ports.append(CustomPortLimit(output, watts, protocols))
    name = item.get('name')
    return ChargingProfile(
        number, name if isinstance(name, str) else '', ports,
        auto_exit=bool(flexible_bool(item.get('auto_exit'))),
        protocols_enabled=bool(flexible_bool(item.get('has_charge_protocol'))),
        profile_id=flexible_int(item.get('id')))


def encode_profile(profile: ChargingProfile, rules: CustomModeRules,
                   mainland: bool) -> dict[str, Any]:
    """Body fields of a profile as the official app sends them."""

    settings = []
    for rule in rules.ports:
        limit = profile.port(rule.output)
        setting: dict[str, Any] = {
            'name': PORT_NAMES[rule.output],
            'power': limit.watts if limit is not None else 0,
            'max_power': rule.maximum(mainland),
        }
        for protocol, key in PROTOCOL_KEYS.items():
            allowed = limit is not None and limit.allows(protocol)
            setting[key] = int(allowed)
        settings.append(setting)
    return {
        'name': profile.name,
        'total_power': profile.total_watts,
        'max_total_power': rules.total_limit,
        'auto_exit': int(profile.auto_exit),
        'has_charge_protocol': int(profile.protocols_enabled),
        'power_settings': settings,
    }


def flexible_int(value: object) -> int | None:
    """Anker sends the same number as an int, a string or a bool."""

    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def flexible_bool(value: object) -> bool | None:
    number = flexible_int(value)
    return None if number is None else number == 1


def _output_named(name: object) -> OutputId | None:
    if not isinstance(name, str):
        return None
    for output, port_name in PORT_NAMES.items():
        if port_name.lower() == name.strip().lower():
            return output
    return None
