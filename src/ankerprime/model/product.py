"""Static product definitions: ports, outputs, capabilities and value ranges."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ankerprime.model.capabilities import Capability, Verification
from ankerprime.model.custom import CustomModeRules, CustomPortRule
from ankerprime.model.enums import (
    ChargingMode, ConnectorType, DisplayLanguage,
    OutputId, PortId, ProductCode, ScreenTimeout)
from ankerprime.model.errors import InvalidParameterError, UnknownProductError


@dataclass(init=False)
class ValueRange:
    """Inclusive integer range with a step, anchored at the minimum."""

    _minimum: int
    _maximum: int
    _step: int

    def __init__(self, minimum: int, maximum: int, step: int = 1):
        if step < 1 or maximum < minimum:
            raise InvalidParameterError(f'invalid range {minimum}..{maximum} '
                                        f'step {step}')
        self._minimum = minimum
        self._maximum = maximum
        self._step = step

    @property
    def minimum(self) -> int:
        return self._minimum

    @property
    def maximum(self) -> int:
        return self._maximum

    @property
    def step(self) -> int:
        return self._step

    def contains(self, value: int) -> bool:
        """Check that the value is in range and on a step boundary."""

        if not self._minimum <= value <= self._maximum:
            return False
        return (value - self._minimum) % self._step == 0


@dataclass(init=False)
class PortSpec:
    """Physical port of a product."""

    _port: PortId
    _connector: ConnectorType
    _max_power: float

    def __init__(self, port: PortId, connector: ConnectorType, max_power: float):
        self._port = port
        self._connector = connector
        self._max_power = max_power

    @property
    def port(self) -> PortId:
        return self._port

    @property
    def connector(self) -> ConnectorType:
        return self._connector

    @property
    def max_power(self) -> float:
        """Maximum output power in watts."""

        return self._max_power


@dataclass(init=False)
class OutputSpec:
    """Switchable output of a product and the ports it controls."""

    _output: OutputId
    _ports: tuple[PortId, ...]

    def __init__(self, output: OutputId, ports: Iterable[PortId]):
        self._output = output
        self._ports = tuple(ports)

    @property
    def output(self) -> OutputId:
        return self._output

    @property
    def ports(self) -> tuple[PortId, ...]:
        return self._ports


@dataclass(init=False)
class Product:
    """Static description of one charger model.

    Capabilities list what the model can do in principle; the firmware of a
    particular unit may narrow them (see ``ChargerState.capabilities``).
    Value lists and ranges apply to the matching capability only.
    """

    _code: ProductCode
    _name: str
    _rated_power: float
    _ports: tuple[PortSpec, ...]
    _outputs: tuple[OutputSpec, ...]
    _capabilities: Mapping[Capability, Verification]

    _charging_modes: tuple[ChargingMode, ...]
    _screen_timeouts: tuple[ScreenTimeout, ...]
    _display_languages: tuple[DisplayLanguage, ...]
    _brightness: ValueRange | None
    _countdown: ValueRange | None
    _priority_outputs: tuple[OutputId, ...]
    _max_preferred_outputs: int
    _custom_mode: CustomModeRules | None

    def __init__(self, *, code: ProductCode, name: str, rated_power: float,
                 ports: Iterable[PortSpec], outputs: Iterable[OutputSpec],
                 capabilities: Mapping[Capability, Verification],
                 charging_modes: Iterable[ChargingMode] = (),
                 screen_timeouts: Iterable[ScreenTimeout] = (),
                 display_languages: Iterable[DisplayLanguage] = (),
                 brightness: ValueRange | None = None,
                 countdown: ValueRange | None = None,
                 priority_outputs: Iterable[OutputId] = (),
                 max_preferred_outputs: int = 0,
                 custom_mode: CustomModeRules | None = None):
        self._code = code
        self._name = name
        self._rated_power = rated_power
        self._ports = tuple(ports)
        self._outputs = tuple(outputs)
        self._capabilities = MappingProxyType(dict(capabilities))

        self._charging_modes = tuple(charging_modes)
        self._screen_timeouts = tuple(screen_timeouts)
        self._display_languages = tuple(display_languages)
        self._brightness = brightness
        self._countdown = countdown
        self._priority_outputs = tuple(priority_outputs)
        self._max_preferred_outputs = max_preferred_outputs
        self._custom_mode = custom_mode

    @property
    def code(self) -> ProductCode:
        return self._code

    @property
    def name(self) -> str:
        return self._name

    @property
    def rated_power(self) -> float:
        """Total rated output power in watts."""

        return self._rated_power

    @property
    def ports(self) -> tuple[PortSpec, ...]:
        return self._ports

    @property
    def outputs(self) -> tuple[OutputSpec, ...]:
        return self._outputs

    @property
    def capabilities(self) -> Mapping[Capability, Verification]:
        return self._capabilities

    @property
    def charging_modes(self) -> tuple[ChargingMode, ...]:
        """Modes selectable with the charging mode capability."""

        return self._charging_modes

    @property
    def screen_timeouts(self) -> tuple[ScreenTimeout, ...]:
        return self._screen_timeouts

    @property
    def display_languages(self) -> tuple[DisplayLanguage, ...]:
        return self._display_languages

    @property
    def brightness(self) -> ValueRange | None:
        """Accepted display brightness, in percent."""

        return self._brightness

    @property
    def countdown(self) -> ValueRange | None:
        """Accepted output countdown duration, in seconds."""

        return self._countdown

    @property
    def priority_outputs(self) -> tuple[OutputId, ...]:
        """Outputs that can be marked preferred in port-priority mode."""

        return self._priority_outputs

    @property
    def max_preferred_outputs(self) -> int:
        return self._max_preferred_outputs

    @property
    def custom_mode(self) -> CustomModeRules | None:
        """Limits of the custom charging mode capability."""

        return self._custom_mode

    def verification(self, capability: Capability) -> Verification | None:
        """Return the trust level of a capability, or None if absent."""

        return self._capabilities.get(capability)

    def supports(self, capability: Capability) -> bool:
        """Check that the capability exists and is implemented."""

        verification = self._capabilities.get(capability)
        return verification is not None and verification != Verification.PLANNED

    def port(self, port: PortId) -> PortSpec:
        for spec in self._ports:
            if spec.port == port:
                return spec
        raise InvalidParameterError(f'{self._code} has no port {port}')

    def output(self, output: OutputId) -> OutputSpec:
        for spec in self._outputs:
            if spec.output == output:
                return spec
        raise InvalidParameterError(f'{self._code} has no output {output}')

    def output_of(self, port: PortId) -> OutputId:
        """Return the output that switches the given port."""

        for spec in self._outputs:
            if port in spec.ports:
                return spec.output
        raise InvalidParameterError(f'{self._code} has no port {port}')


A2345 = Product(
    code=ProductCode.A2345,
    name='Anker Prime Charger 250W',
    rated_power=250,
    ports=(
        PortSpec(PortId.C1, ConnectorType.USB_C, 140),
        PortSpec(PortId.C2, ConnectorType.USB_C, 100),
        PortSpec(PortId.C3, ConnectorType.USB_C, 100),
        PortSpec(PortId.C4, ConnectorType.USB_C, 100),
        PortSpec(PortId.A1, ConnectorType.USB_A, 22.5),
        PortSpec(PortId.A2, ConnectorType.USB_A, 22.5),
    ),
    outputs=(
        OutputSpec(OutputId.C1, (PortId.C1,)),
        OutputSpec(OutputId.C2, (PortId.C2,)),
        OutputSpec(OutputId.C3, (PortId.C3,)),
        OutputSpec(OutputId.C4, (PortId.C4,)),
        OutputSpec(OutputId.USB_A, (PortId.A1, PortId.A2)),
    ),
    capabilities={
        Capability.OUTPUT_SWITCH: Verification.VERIFIED,
        Capability.OUTPUT_COUNTDOWN: Verification.VERIFIED,
        Capability.OUTPUT_SCHEDULE: Verification.VERIFIED,
        Capability.CHARGING_MODE: Verification.VERIFIED,
        Capability.PORT_PRIORITY: Verification.VERIFIED,
        Capability.CUSTOM_CHARGING_MODE: Verification.EXPERIMENTAL,
        Capability.CUSTOM_PROFILES: Verification.EXPERIMENTAL,
        Capability.SCREEN_BRIGHTNESS: Verification.VERIFIED,
        Capability.SCREEN_TIMEOUT: Verification.VERIFIED,
        Capability.KNOB_DIRECTION: Verification.VERIFIED,
        Capability.HOUR_FORMAT: Verification.VERIFIED,
        Capability.CLOCK_SCREENSAVER: Verification.VERIFIED,
        Capability.CLOCK_SCHEDULE: Verification.VERIFIED,
        Capability.HOLIDAY_THEME: Verification.VERIFIED,
        Capability.CLOCK_PICTURE: Verification.EXPERIMENTAL,
        Capability.OWN_CLOCK_PICTURES: Verification.EXPERIMENTAL,
        Capability.MAX_COMPATIBILITY: Verification.VERIFIED,
        Capability.DEVICE_IDENTIFICATION: Verification.VERIFIED,
        Capability.UFCS: Verification.EXPERIMENTAL,
    },
    charging_modes=(
        ChargingMode.AI,
        ChargingMode.PORT_PRIORITY,
        ChargingMode.DUAL_LAPTOP,
        ChargingMode.LOW_CURRENT,
    ),
    screen_timeouts=(
        ScreenTimeout.ALWAYS_ON,
        ScreenTimeout.THIRTY_SECONDS,
        ScreenTimeout.ONE_MINUTE,
        ScreenTimeout.FIVE_MINUTES,
        ScreenTimeout.THIRTY_MINUTES,
    ),
    brightness=ValueRange(20, 100, 5),
    countdown=ValueRange(1, 86_400),
    priority_outputs=(OutputId.C1, OutputId.C2, OutputId.C3, OutputId.C4),
    max_preferred_outputs=2,
    custom_mode=CustomModeRules(
        numbers=range(1, 5),
        total_limit=250,
        ports=(
            CustomPortRule(OutputId.C1, 140),
            CustomPortRule(OutputId.C2, 100, mainland_maximum=120),
            CustomPortRule(OutputId.C3, 100, mainland_maximum=120),
            CustomPortRule(OutputId.C4, 100, mainland_maximum=120),
            CustomPortRule(OutputId.USB_A, 24, choices=(0, 15, 24)),
        ),
        default_watts={OutputId.C1: 100, OutputId.C2: 65, OutputId.C3: 45,
                       OutputId.C4: 25, OutputId.USB_A: 15},
        has_protocols=True,
    ),
)

A2687 = Product(
    code=ProductCode.A2687,
    name='Anker Prime 160W',
    rated_power=160,
    ports=(
        PortSpec(PortId.C1, ConnectorType.USB_C, 160),
        PortSpec(PortId.C2, ConnectorType.USB_C, 160),
        PortSpec(PortId.C3, ConnectorType.USB_C, 160),
    ),
    outputs=(
        OutputSpec(OutputId.C1, (PortId.C1,)),
        OutputSpec(OutputId.C2, (PortId.C2,)),
        OutputSpec(OutputId.C3, (PortId.C3,)),
    ),
    capabilities={
        Capability.OUTPUT_SWITCH: Verification.EXPERIMENTAL,
        Capability.OUTPUT_COUNTDOWN: Verification.EXPERIMENTAL,
        Capability.CHARGING_MODE: Verification.EXPERIMENTAL,
        Capability.FIXED_ALLOCATION: Verification.EXPERIMENTAL,
        Capability.CUSTOM_CHARGING_MODE: Verification.EXPERIMENTAL,
        Capability.SCREEN_BRIGHTNESS: Verification.VERIFIED,
        Capability.SCREEN_TIMEOUT: Verification.VERIFIED,
        Capability.SCREEN_ORIENTATION: Verification.VERIFIED,
        Capability.AUTO_ROTATION: Verification.VERIFIED,
        Capability.DISPLAY_LANGUAGE: Verification.VERIFIED,
        Capability.SCREEN_PICTURE: Verification.EXPERIMENTAL,
    },
    charging_modes=(ChargingMode.AI, ChargingMode.STANDARD),
    custom_mode=CustomModeRules(
        numbers=range(1, 2),
        total_limit=160,
        ports=(
            CustomPortRule(OutputId.C1, 140),
            CustomPortRule(OutputId.C2, 140),
            CustomPortRule(OutputId.C3, 140),
        ),
        default_watts={OutputId.C1: 60, OutputId.C2: 60, OutputId.C3: 40},
        has_protocols=False,
        named=False,
    ),
    screen_timeouts=(
        ScreenTimeout.THIRTY_SECONDS,
        ScreenTimeout.ONE_MINUTE,
        ScreenTimeout.FIVE_MINUTES,
        ScreenTimeout.THIRTY_MINUTES,
        ScreenTimeout.TWELVE_HOURS,
    ),
    display_languages=(
        DisplayLanguage.ENGLISH,
        DisplayLanguage.CHINESE_SIMPLIFIED,
        DisplayLanguage.JAPANESE,
        DisplayLanguage.GERMAN,
    ),
    brightness=ValueRange(25, 100),
    countdown=ValueRange(1, 86_400),
)

PRODUCTS = MappingProxyType({product.code: product for product in (A2345, A2687)})


def get_product(code: str) -> Product:
    """Return the product definition for a model code such as ``A2345``."""

    try:
        return PRODUCTS[ProductCode(code.upper())]
    except ValueError:
        raise UnknownProductError(f'unsupported product: {code}') from None
