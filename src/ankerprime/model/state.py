"""Immutable snapshots of what a charger reports.

Optional values are None when the charger has not reported them (yet) or the
product does not have the feature.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import ClassVar

from ankerprime.model.capabilities import Capability, Verification
from ankerprime.model.custom import ChargingProfile
from ankerprime.model.enums import (
    CableRating, ChargingMode, ChargingProtocol, ClockTheme, DeviceBrand,
    DisplayLanguage, FixedAllocation, HourFormat, KnobDirection,
    OutputId, PortId, ScreenOrientation, ScreenTimeout, Weekday)
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.product import Product


@dataclass(init=False)
class Schedule:
    """Daily switch time of an output; an empty weekday mask runs once."""

    _enabled: bool
    _hour: int
    _minute: int
    _weekdays: Weekday

    def __init__(self, enabled: bool, hour: int, minute: int,
                 weekdays: Weekday = Weekday.ONCE):
        _check_time(hour, minute)
        self._enabled = enabled
        self._hour = hour
        self._minute = minute
        self._weekdays = weekdays

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def hour(self) -> int:
        return self._hour

    @property
    def minute(self) -> int:
        return self._minute

    @property
    def weekdays(self) -> Weekday:
        return self._weekdays


@dataclass(init=False)
class ClockSchedule:
    """Daily time window in which the clock screensaver is shown."""

    _start_hour: int
    _start_minute: int
    _end_hour: int
    _end_minute: int
    _weekdays: Weekday

    def __init__(self, start_hour: int, start_minute: int, end_hour: int,
                 end_minute: int, weekdays: Weekday = Weekday.EVERY_DAY):
        _check_time(start_hour, start_minute)
        _check_time(end_hour, end_minute)
        self._start_hour = start_hour
        self._start_minute = start_minute
        self._end_hour = end_hour
        self._end_minute = end_minute
        self._weekdays = weekdays

    @property
    def start_hour(self) -> int:
        return self._start_hour

    @property
    def start_minute(self) -> int:
        return self._start_minute

    @property
    def end_hour(self) -> int:
        return self._end_hour

    @property
    def end_minute(self) -> int:
        return self._end_minute

    @property
    def weekdays(self) -> Weekday:
        return self._weekdays


@dataclass(init=False)
class Countdown:
    """Running auto-off countdown of an output."""

    _total: int
    _remaining: int
    _reported_at: datetime

    def __init__(self, total: int, remaining: int, reported_at: datetime):
        self._total = total
        self._remaining = remaining
        self._reported_at = reported_at

    @property
    def total(self) -> int:
        """Configured duration in seconds."""

        return self._total

    @property
    def remaining(self) -> int:
        """Seconds left at ``reported_at``."""

        return self._remaining

    @property
    def reported_at(self) -> datetime:
        return self._reported_at

    @property
    def ends_at(self) -> datetime:
        return self._reported_at + timedelta(seconds=self._remaining)


@dataclass(init=False)
class PortReading:
    """Electrical readings and identification data of one port."""

    DELIVERING_MIN_CURRENT: ClassVar[float] = 0.02
    DELIVERING_MIN_VOLTAGE: ClassVar[float] = 3.0

    _port: PortId
    _active: bool
    _status_code: int
    _voltage: float
    _current: float
    _power: float

    _cable: CableRating | None
    _charging_protocol: ChargingProtocol | None
    _device_brand: DeviceBrand | None
    _usb_vendor_id: int | None
    _usb_product_id: int | None

    def __init__(self, port: PortId, *, status_code: int, voltage: float,
                 current: float, power: float, cable: CableRating | None = None,
                 charging_protocol: ChargingProtocol | None = None,
                 device_brand: DeviceBrand | None = None,
                 usb_vendor_id: int | None = None,
                 usb_product_id: int | None = None):
        self._port = port
        self._active = status_code != 0
        self._status_code = status_code
        self._voltage = voltage
        self._current = current
        self._power = power

        self._cable = cable
        self._charging_protocol = charging_protocol
        self._device_brand = device_brand
        self._usb_vendor_id = usb_vendor_id
        self._usb_product_id = usb_product_id

    @property
    def port(self) -> PortId:
        return self._port

    @property
    def active(self) -> bool:
        """The port reports a non-idle status."""

        return self._active

    @property
    def status_code(self) -> int:
        """Raw status byte; only 0 (idle) and non-zero (active) are known."""

        return self._status_code

    @property
    def voltage(self) -> float:
        """Volts."""

        return self._voltage

    @property
    def current(self) -> float:
        """Amperes."""

        return self._current

    @property
    def power(self) -> float:
        """Watts."""

        return self._power

    @property
    def delivering(self) -> bool:
        """A device is drawing a meaningful current."""

        return (self._active
                and self._current > self.DELIVERING_MIN_CURRENT
                and self._voltage > self.DELIVERING_MIN_VOLTAGE)

    @property
    def cable(self) -> CableRating | None:
        return self._cable

    @property
    def charging_protocol(self) -> ChargingProtocol | None:
        return self._charging_protocol

    @property
    def device_brand(self) -> DeviceBrand | None:
        return self._device_brand

    @property
    def usb_vendor_id(self) -> int | None:
        return self._usb_vendor_id

    @property
    def usb_product_id(self) -> int | None:
        return self._usb_product_id


@dataclass(init=False)
class OutputState:
    """Control state of one switchable output."""

    _output: OutputId
    _is_on: bool | None
    _countdown: Countdown | None
    _power_on_schedule: Schedule | None
    _power_off_schedule: Schedule | None
    _preferred: bool | None
    _fault: bool

    def __init__(self, output: OutputId, *, is_on: bool | None = None,
                 countdown: Countdown | None = None,
                 power_on_schedule: Schedule | None = None,
                 power_off_schedule: Schedule | None = None,
                 preferred: bool | None = None, fault: bool = False):
        self._output = output
        self._is_on = is_on
        self._countdown = countdown
        self._power_on_schedule = power_on_schedule
        self._power_off_schedule = power_off_schedule
        self._preferred = preferred
        self._fault = fault

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    @property
    def countdown(self) -> Countdown | None:
        """Running countdown, None when none is running."""

        return self._countdown

    @property
    def power_on_schedule(self) -> Schedule | None:
        """None when the slot was never set."""

        return self._power_on_schedule

    @property
    def power_off_schedule(self) -> Schedule | None:
        """None when the slot was never set."""

        return self._power_off_schedule

    @property
    def preferred(self) -> bool | None:
        """Marked preferred in port-priority mode; None outside that mode."""

        return self._preferred

    @property
    def fault(self) -> bool:
        return self._fault


@dataclass(init=False)
class DisplaySettings:
    """Display settings; each product reports a subset."""

    _brightness: int | None
    _screen_timeout: ScreenTimeout | None
    _orientation: ScreenOrientation | None
    _auto_rotation: bool | None
    _language: DisplayLanguage | None
    _knob_direction: KnobDirection | None
    _hour_format: HourFormat | None
    _screen_picture: int | None

    def __init__(self, *, brightness: int | None = None,
                 screen_timeout: ScreenTimeout | None = None,
                 orientation: ScreenOrientation | None = None,
                 auto_rotation: bool | None = None,
                 language: DisplayLanguage | None = None,
                 knob_direction: KnobDirection | None = None,
                 hour_format: HourFormat | None = None,
                 screen_picture: int | None = None):
        self._brightness = brightness
        self._screen_timeout = screen_timeout
        self._orientation = orientation
        self._auto_rotation = auto_rotation
        self._language = language
        self._knob_direction = knob_direction
        self._hour_format = hour_format
        self._screen_picture = screen_picture

    @property
    def brightness(self) -> int | None:
        """Percent."""

        return self._brightness

    @property
    def screen_timeout(self) -> ScreenTimeout | None:
        return self._screen_timeout

    @property
    def orientation(self) -> ScreenOrientation | None:
        return self._orientation

    @property
    def auto_rotation(self) -> bool | None:
        return self._auto_rotation

    @property
    def language(self) -> DisplayLanguage | None:
        return self._language

    @property
    def knob_direction(self) -> KnobDirection | None:
        return self._knob_direction

    @property
    def hour_format(self) -> HourFormat | None:
        return self._hour_format

    @property
    def screen_picture(self) -> int | None:
        """Screen picture shown (160W): low 16 bits of its id."""

        return self._screen_picture


@dataclass(init=False)
class ClockSettings:
    """Clock screensaver settings (250W)."""

    _enabled: bool
    _theme: ClockTheme | None
    _holiday_theme: bool | None
    _schedule: ClockSchedule | None
    _picture_id: int | None
    _picture_time: bool | None

    def __init__(self, enabled: bool, *, theme: ClockTheme | None = None,
                 holiday_theme: bool | None = None,
                 schedule: ClockSchedule | None = None,
                 picture_id: int | None = None,
                 picture_time: bool | None = None):
        self._enabled = enabled
        self._theme = theme
        self._holiday_theme = holiday_theme
        self._schedule = schedule
        self._picture_id = picture_id
        self._picture_time = picture_time

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def theme(self) -> ClockTheme | None:
        """None for a theme code this library does not know."""

        return self._theme

    @property
    def holiday_theme(self) -> bool | None:
        return self._holiday_theme

    @property
    def schedule(self) -> ClockSchedule | None:
        return self._schedule

    @property
    def picture_id(self) -> int | None:
        """Id of the picture shown with a picture or own-picture theme."""

        return self._picture_id

    @property
    def picture_time(self) -> bool | None:
        """The time is shown over own pictures."""

        return self._picture_time


@dataclass(init=False)
class ChargerSettings:
    """Charger-wide settings."""

    _charging_mode: ChargingMode | None
    _charging_mode_code: int | None
    _fixed_allocation: FixedAllocation | None
    _max_compatibility: bool | None
    _device_identification: bool | None
    _ufcs: bool | None
    _updating_firmware: bool | None
    _display: DisplaySettings
    _clock: ClockSettings | None
    _custom_mode_enabled: bool | None
    _active_profile: int | None
    _custom_profiles: tuple[ChargingProfile, ...] | None

    def __init__(self, *, charging_mode: ChargingMode | None = None,
                 charging_mode_code: int | None = None,
                 fixed_allocation: FixedAllocation | None = None,
                 max_compatibility: bool | None = None,
                 device_identification: bool | None = None,
                 ufcs: bool | None = None,
                 updating_firmware: bool | None = None,
                 display: DisplaySettings | None = None,
                 clock: ClockSettings | None = None,
                 custom_mode_enabled: bool | None = None,
                 active_profile: int | None = None,
                 custom_profiles: Iterable[ChargingProfile] | None = None):
        self._charging_mode = charging_mode
        self._charging_mode_code = charging_mode_code
        self._fixed_allocation = fixed_allocation
        self._max_compatibility = max_compatibility
        self._device_identification = device_identification
        self._ufcs = ufcs
        self._updating_firmware = updating_firmware
        self._display = display if display is not None else DisplaySettings()
        self._clock = clock
        self._custom_mode_enabled = custom_mode_enabled
        self._active_profile = active_profile
        self._custom_profiles = None
        if custom_profiles is not None:
            self._custom_profiles = tuple(custom_profiles)

    @property
    def charging_mode(self) -> ChargingMode | None:
        """None when not reported or the code is unknown to this library."""

        return self._charging_mode

    @property
    def charging_mode_code(self) -> int | None:
        """Raw mode code as reported, kept for codes without a name."""

        return self._charging_mode_code

    @property
    def fixed_allocation(self) -> FixedAllocation | None:
        return self._fixed_allocation

    @property
    def max_compatibility(self) -> bool | None:
        return self._max_compatibility

    @property
    def device_identification(self) -> bool | None:
        return self._device_identification

    @property
    def ufcs(self) -> bool | None:
        return self._ufcs

    @property
    def updating_firmware(self) -> bool | None:
        return self._updating_firmware

    @property
    def display(self) -> DisplaySettings:
        return self._display

    @property
    def clock(self) -> ClockSettings | None:
        return self._clock

    @property
    def custom_mode_enabled(self) -> bool | None:
        """The account's switch that makes custom mode available."""

        return self._custom_mode_enabled

    @property
    def active_profile(self) -> int | None:
        """Number of the custom profile in use; None outside custom mode."""

        return self._active_profile

    @property
    def custom_profiles(self) -> tuple[ChargingProfile, ...] | None:
        """Profiles stored in the account, by number; None until read."""

        return self._custom_profiles

    def custom_profile(self, number: int) -> ChargingProfile | None:
        for profile in self._custom_profiles or ():
            if profile.number == number:
                return profile
        return None


@dataclass(init=False)
class DeviceInfo:
    """Identity of one charger unit."""

    _serial: str | None
    _name: str | None
    _firmware_version: str | None
    _hardware_version: str | None
    _mac_address: str | None

    def __init__(self, *, serial: str | None = None, name: str | None = None,
                 firmware_version: str | None = None,
                 hardware_version: str | None = None,
                 mac_address: str | None = None):
        self._serial = serial
        self._name = name
        self._firmware_version = firmware_version
        self._hardware_version = hardware_version
        self._mac_address = mac_address

    @property
    def serial(self) -> str | None:
        return self._serial

    @property
    def name(self) -> str | None:
        """User-assigned name, when the connection provides one."""

        return self._name

    @property
    def firmware_version(self) -> str | None:
        return self._firmware_version

    @property
    def hardware_version(self) -> str | None:
        return self._hardware_version

    @property
    def mac_address(self) -> str | None:
        return self._mac_address


@dataclass(init=False)
class ChargerState:
    """Complete snapshot of one charger, as published to listeners."""

    _product: Product
    _info: DeviceInfo
    _available: bool
    _capabilities: Mapping[Capability, Verification]
    _ports: Mapping[PortId, PortReading]
    _outputs: Mapping[OutputId, OutputState]
    _settings: ChargerSettings
    _over_temperature: bool | None
    _error_code: int | None
    _energy: Mapping[PortId, float]
    _updated_at: datetime | None

    def __init__(self, product: Product, *, info: DeviceInfo | None = None,
                 available: bool = False,
                 capabilities: Mapping[Capability, Verification] | None = None,
                 ports: Mapping[PortId, PortReading] | None = None,
                 outputs: Mapping[OutputId, OutputState] | None = None,
                 settings: ChargerSettings | None = None,
                 over_temperature: bool | None = None,
                 error_code: int | None = None,
                 energy: Mapping[PortId, float] | None = None,
                 updated_at: datetime | None = None):
        if capabilities is None:
            capabilities = product.capabilities
        self._product = product
        self._info = info if info is not None else DeviceInfo()
        self._available = available
        self._capabilities = MappingProxyType(dict(capabilities))
        self._ports = MappingProxyType(dict(ports or {}))
        self._outputs = MappingProxyType(dict(outputs or {}))
        self._settings = settings if settings is not None else ChargerSettings()
        self._over_temperature = over_temperature
        self._error_code = error_code
        self._energy = MappingProxyType(dict(energy or {}))
        self._updated_at = updated_at

    @property
    def product(self) -> Product:
        return self._product

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def available(self) -> bool:
        """The charger is reachable and its readings are current."""

        return self._available

    @property
    def capabilities(self) -> Mapping[Capability, Verification]:
        """Product capabilities narrowed by what this unit's firmware reports."""

        return self._capabilities

    @property
    def ports(self) -> Mapping[PortId, PortReading]:
        return self._ports

    @property
    def outputs(self) -> Mapping[OutputId, OutputState]:
        return self._outputs

    @property
    def settings(self) -> ChargerSettings:
        return self._settings

    @property
    def over_temperature(self) -> bool | None:
        return self._over_temperature

    @property
    def error_code(self) -> int | None:
        """Raw fault code as reported, None when not reported."""

        return self._error_code

    @property
    def energy(self) -> Mapping[PortId, float]:
        """Cumulative energy per port in watt-hours."""

        return self._energy

    @property
    def total_energy(self) -> float:
        """Cumulative energy of all ports in watt-hours."""

        return sum(self._energy.values())

    @property
    def total_power(self) -> float:
        """Sum of the power of all active ports, in watts."""

        return sum(reading.power for reading in self._ports.values()
                   if reading.active)

    @property
    def updated_at(self) -> datetime | None:
        """When the charger last reported anything."""

        return self._updated_at

    def supports(self, capability: Capability) -> bool:
        """Check that this unit has the capability and it is implemented."""

        verification = self._capabilities.get(capability)
        return verification is not None and verification != Verification.PLANNED


def _check_time(hour: int, minute: int) -> None:
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise InvalidParameterError(f'invalid time {hour}:{minute}')
