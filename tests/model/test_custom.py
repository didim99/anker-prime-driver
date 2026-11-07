"""Custom charging profiles and their rules."""

import pytest

from ankerprime.model import (
    A2345, AccountSettings, ChargingProfile, CustomModeRules,
    CustomPortLimit, CustomProtocol, InvalidParameterError, OutputId)


def rules() -> CustomModeRules:
    assert A2345.custom_mode is not None
    return A2345.custom_mode


def test_template_follows_the_official_defaults() -> None:
    profile = rules().template(2, 'Desk')
    assert profile.number == 2
    assert profile.total_watts == 250
    assert [port.watts for port in profile.ports] == [100, 65, 45, 25, 15]
    c4 = profile.port(OutputId.C4)
    assert c4 is not None
    assert c4.allows(CustomProtocol.SCP) and c4.allows(CustomProtocol.PPS_11V)
    assert not c4.allows(CustomProtocol.PPS_16V)
    assert not c4.allows(CustomProtocol.PD_12V)
    usb_a = profile.port(OutputId.USB_A)
    assert usb_a is not None
    assert usb_a.protocols == {CustomProtocol.UFCS}
    rules().validate(profile)


@pytest.mark.parametrize('output, watts, mainland', [
    (OutputId.C1, 141, False),
    (OutputId.C1, 10, False),
    (OutputId.C2, 101, False),
    (OutputId.C2, 121, True),
    (OutputId.USB_A, 20, False),
])
def test_port_limits(output: OutputId, watts: int, mainland: bool) -> None:
    limit = CustomPortLimit(output, watts)
    profile = rules().template(1, 'P').with_port(limit)
    with pytest.raises(InvalidParameterError, match='accepts'):
        rules().validate(profile, mainland)


def test_mainland_allows_more_on_c2_to_c4() -> None:
    profile = rules().template(1, 'P')
    profile = profile.with_port(CustomPortLimit(OutputId.C1, 15))
    profile = profile.with_port(CustomPortLimit(OutputId.C2, 120))
    rules().validate(profile, mainland=True)
    with pytest.raises(InvalidParameterError):
        rules().validate(profile)


def test_total_number_and_name() -> None:
    template = rules().template(1, 'P')
    with pytest.raises(InvalidParameterError, match='total'):
        rules().validate(template.with_port(
            CustomPortLimit(OutputId.C1, 140)))
    with pytest.raises(InvalidParameterError, match='number'):
        rules().validate(template.replace(number=5))
    with pytest.raises(InvalidParameterError, match='name'):
        rules().validate(template.replace(name='   '))
    with pytest.raises(InvalidParameterError, match='name'):
        rules().validate(template.replace(name='x' * 21))


def test_protocol_rules() -> None:
    voltages = {CustomProtocol.PD_12V, CustomProtocol.PPS_11V,
                CustomProtocol.PPS_16V, CustomProtocol.PPS_21V}
    profile = rules().template(1, 'P').with_port(
        CustomPortLimit(OutputId.C1, 100, voltages))
    with pytest.raises(InvalidParameterError, match='at most 3'):
        rules().validate(profile)
    both = {CustomProtocol.PD_12V, CustomProtocol.XIAOMI}
    profile = rules().template(1, 'P').with_port(
        CustomPortLimit(OutputId.C1, 100, both))
    rules().validate(profile)
    with pytest.raises(InvalidParameterError, match='Xiaomi'):
        rules().validate(profile, mainland=True)


def test_normalize_trims_and_drops_scp_on_low_ports() -> None:
    profile = ChargingProfile(3, '  Night  ', [
        CustomPortLimit(OutputId.C2, 20, {CustomProtocol.SCP,
                                          CustomProtocol.UFCS}),
    ], protocols_enabled=False)
    normalized = rules().normalize(profile)
    assert normalized.name == 'Night'
    assert normalized.protocols_enabled
    assert [port.output for port in normalized.ports] == [
        OutputId.C1, OutputId.C2, OutputId.C3, OutputId.C4, OutputId.USB_A]
    assert normalized.watts(OutputId.C1) == 0
    c2 = normalized.port(OutputId.C2)
    assert c2 is not None and c2.protocols == {CustomProtocol.UFCS}
    mainland = rules().normalize(profile, mainland=True)
    c2 = mainland.port(OutputId.C2)
    assert c2 is not None and CustomProtocol.SCP in c2.protocols


def test_region_protocols() -> None:
    assert CustomProtocol.XIAOMI in CustomModeRules.protocols(mainland=True)
    assert CustomProtocol.SCP not in CustomModeRules.protocols(mainland=True)
    assert CustomProtocol.SCP in CustomModeRules.protocols()


def test_account_settings_replace_keeps_unset_values() -> None:
    settings = AccountSettings(custom_mode_enabled=True,
                               max_compatibility=False)
    changed = settings.replace(max_compatibility=True)
    assert changed.custom_mode_enabled is True
    assert changed.max_compatibility is True
    assert changed.device_identification is None
