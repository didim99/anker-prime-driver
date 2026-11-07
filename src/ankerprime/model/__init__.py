"""Product definitions, capabilities and charger state shared by all layers."""

from ankerprime.model.capabilities import Capability, Verification
from ankerprime.model.enums import (
    CableRating, ChargingMode, ChargingProtocol, ClockTheme,
    ConnectorType, DeviceBrand, DisplayLanguage, FixedAllocation,
    HourFormat, KnobDirection, OutputId, PortId, ProductCode,
    ScheduleSlot, ScreenOrientation, ScreenTimeout, Weekday)
from ankerprime.model.errors import (
    AccountRefusedError, AnkerPrimeError, AuthenticationError,
    ChargerConnectionError, ChargerTimeoutError,
    InvalidParameterError, ProtocolError, UnknownProductError,
    UnsupportedCapabilityError, WriteRejectedError)
from ankerprime.model.product import (
    A2345, A2687, PRODUCTS, OutputSpec,
    PortSpec, Product, ValueRange, get_product)
from ankerprime.model.state import (
    ChargerSettings, ChargerState, ClockSchedule, ClockSettings, Countdown,
    DeviceInfo, DisplaySettings, OutputState, PortReading, Schedule)


__all__ = [
    'A2345',
    'A2687',
    'PRODUCTS',
    'AccountRefusedError',
    'AnkerPrimeError',
    'AuthenticationError',
    'CableRating',
    'Capability',
    'ChargerConnectionError',
    'ChargerSettings',
    'ChargerState',
    'ChargerTimeoutError',
    'ChargingMode',
    'ChargingProtocol',
    'ClockSchedule',
    'ClockSettings',
    'ClockTheme',
    'ConnectorType',
    'Countdown',
    'DeviceBrand',
    'DeviceInfo',
    'DisplayLanguage',
    'DisplaySettings',
    'FixedAllocation',
    'HourFormat',
    'InvalidParameterError',
    'KnobDirection',
    'OutputId',
    'OutputSpec',
    'OutputState',
    'PortId',
    'PortReading',
    'PortSpec',
    'Product',
    'ProductCode',
    'ProtocolError',
    'Schedule',
    'ScheduleSlot',
    'ScreenOrientation',
    'ScreenTimeout',
    'UnknownProductError',
    'UnsupportedCapabilityError',
    'ValueRange',
    'Verification',
    'Weekday',
    'WriteRejectedError',
    'get_product',
]
