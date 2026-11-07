"""Custom charging mode: profiles, their limits and account settings.

A custom profile caps the power of every output; the 250W keeps up to four
of them in the Anker account and also lets each USB-C port allow or block
single fast-charge protocols. Accounts of mainland China have higher limits
on C2–C4 and a different protocol set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ankerprime.model.enums import OutputId
from ankerprime.model.errors import InvalidParameterError


class CustomProtocol(StrEnum):
    """Fast-charge protocol a custom profile allows on a port."""

    SCP = 'scp'
    """Huawei SCP on overseas firmware."""

    HUAWEI = 'huawei'
    """Huawei SCP on mainland firmware."""

    UFCS = 'ufcs'
    PD_12V = 'pd_12v'
    PPS_11V = 'pps_11v'
    PPS_16V = 'pps_16v'
    PPS_21V = 'pps_21v'
    XIAOMI = 'xiaomi'
    """Xiaomi HyperCharge; mainland firmware only."""


VOLTAGE_PROFILES = frozenset({
    CustomProtocol.PD_12V, CustomProtocol.PPS_11V,
    CustomProtocol.PPS_16V, CustomProtocol.PPS_21V,
})
OVERSEAS_PROTOCOLS = (
    CustomProtocol.SCP, CustomProtocol.UFCS, CustomProtocol.PD_12V,
    CustomProtocol.PPS_11V, CustomProtocol.PPS_16V,
    CustomProtocol.PPS_21V,
)
MAINLAND_PROTOCOLS = (
    CustomProtocol.HUAWEI, CustomProtocol.UFCS, CustomProtocol.PD_12V,
    CustomProtocol.PPS_11V, CustomProtocol.PPS_16V,
    CustomProtocol.PPS_21V, CustomProtocol.XIAOMI,
)


@dataclass(init=False)
class CustomPortLimit:
    """Power cap and allowed protocols of one output in a profile."""

    _output: OutputId
    _watts: int
    _protocols: frozenset[CustomProtocol]

    def __init__(self, output: OutputId, watts: int,
                 protocols: Iterable[CustomProtocol] = ()):
        self._output = output
        self._watts = watts
        self._protocols = frozenset(protocols)

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def watts(self) -> int:
        """0 switches the output off while the profile is active."""

        return self._watts

    @property
    def protocols(self) -> frozenset[CustomProtocol]:
        return self._protocols

    def allows(self, protocol: CustomProtocol) -> bool:
        return protocol in self._protocols


@dataclass(init=False)
class ChargingProfile:
    """One custom charging profile.

    ``profile_id`` is the account's id of a stored profile, None for one not
    saved yet. ``protocols_enabled`` tells whether the per-port protocols
    take part; profiles saved by older app versions have none.
    """

    _number: int
    _name: str
    _auto_exit: bool
    _ports: tuple[CustomPortLimit, ...]
    _protocols_enabled: bool
    _profile_id: int | None

    def __init__(self, number: int, name: str,
                 ports: Iterable[CustomPortLimit], *,
                 auto_exit: bool = False, protocols_enabled: bool = True,
                 profile_id: int | None = None):
        self._number = number
        self._name = name
        self._auto_exit = auto_exit
        self._ports = tuple(ports)
        self._protocols_enabled = protocols_enabled
        self._profile_id = profile_id

    @property
    def number(self) -> int:
        return self._number

    @property
    def name(self) -> str:
        return self._name

    @property
    def auto_exit(self) -> bool:
        """Leave custom mode once every device is unplugged."""

        return self._auto_exit

    @property
    def ports(self) -> tuple[CustomPortLimit, ...]:
        return self._ports

    @property
    def protocols_enabled(self) -> bool:
        return self._protocols_enabled

    @property
    def profile_id(self) -> int | None:
        return self._profile_id

    @property
    def total_watts(self) -> int:
        return sum(port.watts for port in self._ports)

    def port(self, output: OutputId) -> CustomPortLimit | None:
        for port in self._ports:
            if port.output == output:
                return port
        return None

    def watts(self, output: OutputId) -> int:
        """Cap of one output; 0 for outputs the profile leaves out."""

        port = self.port(output)
        return port.watts if port is not None else 0

    def replace(self, *, number: int | None = None, name: str | None = None,
                ports: Iterable[CustomPortLimit] | None = None,
                auto_exit: bool | None = None,
                protocols_enabled: bool | None = None,
                profile_id: int | None = None) -> ChargingProfile:
        """Copy with the given fields changed."""

        return ChargingProfile(
            self._number if number is None else number,
            self._name if name is None else name,
            self._ports if ports is None else ports,
            auto_exit=self._auto_exit if auto_exit is None else auto_exit,
            protocols_enabled=(self._protocols_enabled
                               if protocols_enabled is None
                               else protocols_enabled),
            profile_id=self._profile_id if profile_id is None else profile_id)

    def with_port(self, limit: CustomPortLimit) -> ChargingProfile:
        """Copy with one output's limit replaced or added."""

        ports = [port for port in self._ports if port.output != limit.output]
        ports.append(limit)
        return self.replace(ports=ports)


@dataclass(init=False)
class CustomPortRule:
    """Which caps one output accepts in custom mode."""

    _output: OutputId
    _maximum: int
    _mainland_maximum: int
    _choices: tuple[int, ...] | None
    _minimum: int

    def __init__(self, output: OutputId, maximum: int, *,
                 mainland_maximum: int | None = None,
                 choices: Iterable[int] | None = None, minimum: int = 15):
        self._output = output
        self._maximum = maximum
        self._mainland_maximum = (maximum if mainland_maximum is None
                                  else mainland_maximum)
        self._choices = tuple(choices) if choices is not None else None
        self._minimum = minimum

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def minimum(self) -> int:
        """Lowest non-zero cap."""

        return self._minimum

    @property
    def choices(self) -> tuple[int, ...] | None:
        """The only caps allowed, when the output has fixed steps."""

        return self._choices

    def maximum(self, mainland: bool = False) -> int:
        return self._mainland_maximum if mainland else self._maximum

    def allows(self, watts: int, mainland: bool = False) -> bool:
        if self._choices is not None:
            return watts in self._choices
        return watts == 0 or self._minimum <= watts <= self.maximum(mainland)


@dataclass(init=False)
class CustomModeRules:
    """Limits of a product's custom charging mode."""

    _numbers: range
    _total_limit: int
    _ports: tuple[CustomPortRule, ...]
    _default_watts: Mapping[OutputId, int]
    _has_protocols: bool
    _name_length: int
    _max_voltage_profiles: int

    def __init__(self, *, numbers: range, total_limit: int,
                 ports: Iterable[CustomPortRule],
                 default_watts: Mapping[OutputId, int],
                 has_protocols: bool, name_length: int = 20,
                 max_voltage_profiles: int = 3):
        self._numbers = numbers
        self._total_limit = total_limit
        self._ports = tuple(ports)
        self._default_watts = MappingProxyType(dict(default_watts))
        self._has_protocols = has_protocols
        self._name_length = name_length
        self._max_voltage_profiles = max_voltage_profiles

    @property
    def numbers(self) -> range:
        """Profile numbers the charger knows."""

        return self._numbers

    @property
    def total_limit(self) -> int:
        return self._total_limit

    @property
    def ports(self) -> tuple[CustomPortRule, ...]:
        return self._ports

    @property
    def has_protocols(self) -> bool:
        """Profiles carry per-port protocol choices."""

        return self._has_protocols

    @property
    def name_length(self) -> int:
        return self._name_length

    @property
    def max_voltage_profiles(self) -> int:
        """Most PD 12V and PPS ranges one port may allow."""

        return self._max_voltage_profiles

    def port(self, output: OutputId) -> CustomPortRule:
        for rule in self._ports:
            if rule.output == output:
                return rule
        raise InvalidParameterError(f'no custom limit for output {output}')

    @staticmethod
    def protocols(mainland: bool = False) -> tuple[CustomProtocol, ...]:
        """Protocols the firmware of the account's region offers."""

        return MAINLAND_PROTOCOLS if mainland else OVERSEAS_PROTOCOLS

    def template(self, number: int, name: str, mainland: bool = False
                 ) -> ChargingProfile:
        """A new profile with the official editor's defaults.

        Protocols follow each port's cap: SCP, PPS 11V and Huawei from 21 W,
        PPS 16V and 21V from 45 W, UFCS always, PD 12V and Xiaomi never.
        """

        ports = []
        for rule in self._ports:
            watts = self._default_watts.get(rule.output, 0)
            protocols: set[CustomProtocol] = set()
            if self._has_protocols:
                protocols.add(CustomProtocol.UFCS)
                if watts >= 21:
                    protocols |= {CustomProtocol.SCP,
                                  CustomProtocol.PPS_11V,
                                  CustomProtocol.HUAWEI}
                if watts >= 45:
                    protocols |= {CustomProtocol.PPS_16V,
                                  CustomProtocol.PPS_21V}
            ports.append(CustomPortLimit(rule.output, watts, protocols))
        return ChargingProfile(number, name, ports,
                               protocols_enabled=self._has_protocols)

    def normalize(self, profile: ChargingProfile, mainland: bool = False
                  ) -> ChargingProfile:
        """Prepare a profile for saving as the official app does.

        Trims the name, lists every output in the product's order and, on
        overseas firmware, drops SCP from ports capped at 15–20 W.
        """

        ports = []
        for rule in self._ports:
            limit = profile.port(rule.output)
            watts = limit.watts if limit is not None else 0
            protocols = set(limit.protocols) if limit is not None else set()
            if not mainland and 15 <= watts <= 20:
                protocols.discard(CustomProtocol.SCP)
            ports.append(CustomPortLimit(rule.output, watts, protocols))
        return profile.replace(name=profile.name.strip(), ports=ports,
                               protocols_enabled=self._has_protocols)

    def validate(self, profile: ChargingProfile, mainland: bool = False
                 ) -> None:
        """Raise ``InvalidParameterError`` for a profile the charger refuses."""

        if profile.number not in self._numbers:
            raise InvalidParameterError(
                f'profile number must be {self._numbers.start}–'
                f'{self._numbers.stop - 1}')
        name = profile.name.strip()
        if not 1 <= len(name) <= self._name_length:
            raise InvalidParameterError(
                f'profile name must have 1–{self._name_length} characters')
        known = {rule.output for rule in self._ports}
        for limit in profile.ports:
            if limit.output not in known:
                raise InvalidParameterError(
                    f'no custom limit for output {limit.output}')
            self._check_port(limit, mainland)
        if profile.total_watts > self._total_limit:
            raise InvalidParameterError(
                f'total {profile.total_watts} W exceeds '
                f'{self._total_limit} W')

    def _check_port(self, limit: CustomPortLimit, mainland: bool) -> None:
        rule = self.port(limit.output)
        if not rule.allows(limit.watts, mainland):
            if rule.choices is not None:
                allowed = ', '.join(map(str, rule.choices))
            else:
                allowed = f'0 or {rule.minimum}–{rule.maximum(mainland)}'
            raise InvalidParameterError(
                f'{limit.output} accepts {allowed} W, not {limit.watts}')
        if not self._has_protocols:
            return
        voltages = len(limit.protocols & VOLTAGE_PROFILES)
        if voltages > self._max_voltage_profiles:
            raise InvalidParameterError(
                f'{limit.output} allows at most '
                f'{self._max_voltage_profiles} of PD 12V and the PPS ranges')
        exclusive = {CustomProtocol.PD_12V, CustomProtocol.XIAOMI}
        if mainland and exclusive <= limit.protocols:
            raise InvalidParameterError(
                f'{limit.output} cannot allow both PD 12V and Xiaomi')


@dataclass(init=False)
class AccountSettings:
    """Charger settings the Anker account keeps; None when not reported."""

    _custom_mode_enabled: bool | None
    _max_compatibility: bool | None
    _device_identification: bool | None

    def __init__(self, *, custom_mode_enabled: bool | None = None,
                 max_compatibility: bool | None = None,
                 device_identification: bool | None = None):
        self._custom_mode_enabled = custom_mode_enabled
        self._max_compatibility = max_compatibility
        self._device_identification = device_identification

    @property
    def custom_mode_enabled(self) -> bool | None:
        """The app's Labs switch that makes custom mode available."""

        return self._custom_mode_enabled

    @property
    def max_compatibility(self) -> bool | None:
        return self._max_compatibility

    @property
    def device_identification(self) -> bool | None:
        return self._device_identification

    def replace(self, *, custom_mode_enabled: bool | None = None,
                max_compatibility: bool | None = None,
                device_identification: bool | None = None
                ) -> AccountSettings:
        def pick(new: bool | None, old: bool | None) -> bool | None:
            return old if new is None else new

        return AccountSettings(
            custom_mode_enabled=pick(custom_mode_enabled,
                                     self._custom_mode_enabled),
            max_compatibility=pick(max_compatibility,
                                   self._max_compatibility),
            device_identification=pick(device_identification,
                                       self._device_identification))
