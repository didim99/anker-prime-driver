"""Product definition tests."""

import pytest

from ankerprime.model import (
    A2345, A2687, Capability, ChargingMode,
    InvalidParameterError, OutputId, PortId, Product, ProductCode,
    UnknownProductError, ValueRange, Verification, get_product)


def test_a2345_ports_and_outputs() -> None:
    assert [spec.port for spec in A2345.ports] == list(PortId)
    assert [spec.output for spec in A2345.outputs] == list(OutputId)
    assert A2345.output(OutputId.USB_A).ports == (PortId.A1, PortId.A2)
    assert A2345.port(PortId.C1).max_power == 140
    assert sum(spec.max_power for spec in A2345.ports) > A2345.rated_power


@pytest.mark.parametrize(('port', 'output'), [
    (PortId.C1, OutputId.C1),
    (PortId.C4, OutputId.C4),
    (PortId.A1, OutputId.USB_A),
    (PortId.A2, OutputId.USB_A),
])
def test_a2345_output_of_port(port: PortId, output: OutputId) -> None:
    assert A2345.output_of(port) == output


def test_a2687_has_three_independent_ports() -> None:
    ports = [spec.port for spec in A2687.ports]
    assert ports == [PortId.C1, PortId.C2, PortId.C3]
    for spec in A2687.ports:
        assert A2687.output_of(spec.port) == OutputId(spec.port.value)


def test_unknown_port_and_output_raise() -> None:
    with pytest.raises(InvalidParameterError):
        A2687.port(PortId.C4)
    with pytest.raises(InvalidParameterError):
        A2687.output(OutputId.USB_A)
    with pytest.raises(InvalidParameterError):
        A2687.output_of(PortId.A1)


def test_capability_lookup() -> None:
    assert A2345.verification(Capability.OUTPUT_SWITCH) == Verification.VERIFIED
    assert A2345.supports(Capability.OUTPUT_SCHEDULE)
    assert A2687.supports(Capability.SCREEN_PICTURE)
    assert not A2345.supports(Capability.SCREEN_ORIENTATION)
    assert A2345.verification(Capability.SCREEN_ORIENTATION) is None
    assert A2687.verification(Capability.SCREEN_BRIGHTNESS) == Verification.VERIFIED
    assert not A2687.supports(Capability.OUTPUT_SCHEDULE)


def test_planned_capabilities_are_not_supported() -> None:
    planned = {Capability.SCREEN_PICTURE: Verification.PLANNED}
    product = Product(code=ProductCode.A2687, name='Test', rated_power=10,
                      ports=(), outputs=(), capabilities=planned)
    assert product.verification(Capability.SCREEN_PICTURE) == (
        Verification.PLANNED)
    assert not product.supports(Capability.SCREEN_PICTURE)


def test_capabilities_are_read_only() -> None:
    capabilities = A2345.capabilities
    with pytest.raises(TypeError):
        capabilities[Capability.UFCS] = Verification.VERIFIED  # type: ignore[index]


def test_charging_modes_exclude_custom() -> None:
    assert ChargingMode.CUSTOM not in A2345.charging_modes
    assert A2687.charging_modes == (ChargingMode.AI, ChargingMode.STANDARD)


@pytest.mark.parametrize(('code', 'product_code'), [
    ('A2345', ProductCode.A2345),
    ('a2687', ProductCode.A2687),
])
def test_get_product(code: str, product_code: ProductCode) -> None:
    assert get_product(code).code == product_code


def test_get_unknown_product_raises() -> None:
    with pytest.raises(UnknownProductError):
        get_product('A1234')


@pytest.mark.parametrize(('value', 'expected'), [
    (19, False), (20, True), (25, True), (27, False), (100, True), (105, False),
])
def test_value_range_contains(value: int, expected: bool) -> None:
    assert ValueRange(20, 100, 5).contains(value) is expected


@pytest.mark.parametrize(('minimum', 'maximum', 'step'), [
    (10, 5, 1), (0, 10, 0),
])
def test_value_range_rejects_invalid_bounds(
        minimum: int, maximum: int, step: int) -> None:
    with pytest.raises(InvalidParameterError):
        ValueRange(minimum, maximum, step)
