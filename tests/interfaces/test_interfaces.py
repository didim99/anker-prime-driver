"""Interface contract tests."""

import pytest

from ankerprime.interfaces import Channel, Charger, DocumentStore


@pytest.mark.parametrize('interface', [Channel, Charger, DocumentStore])
def test_interfaces_are_abstract(interface: type) -> None:
    with pytest.raises(TypeError):
        interface()


def test_charger_declares_a_method_per_capability_command() -> None:
    expected = {
        'set_output', 'start_countdown', 'cancel_countdown', 'set_schedule',
        'set_charging_mode', 'set_preferred_outputs', 'set_fixed_allocation',
        'set_brightness', 'set_screen_timeout', 'set_screen_orientation',
        'set_auto_rotation', 'set_display_language', 'set_knob_direction',
        'set_hour_format', 'set_clock', 'set_clock_schedule',
        'set_holiday_theme', 'set_max_compatibility',
        'set_device_identification', 'set_ufcs',
    }
    assert expected <= Charger.__abstractmethods__
