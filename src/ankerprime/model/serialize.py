"""JSON-compatible snapshots of the charger state (CLI output, diagnostics)."""

from datetime import datetime
from typing import Any

from ankerprime.model.state import (
    ChargerSettings, ChargerState, ClockSchedule,
    Countdown, OutputState, PortReading, Schedule)
from ankerprime.utils import enum_value


def state_to_dict(state: ChargerState) -> dict[str, Any]:
    """Everything a state holds, with enums as their string values."""

    info = state.info
    capabilities = {capability.value: verification.value
                    for capability, verification in state.capabilities.items()}
    return {
        'product': {'code': state.product.code.value,
                    'name': state.product.name},
        'info': {'serial': info.serial, 'name': info.name,
                 'firmware_version': info.firmware_version,
                 'hardware_version': info.hardware_version,
                 'mac_address': info.mac_address},
        'available': state.available,
        'updated_at': _time(state.updated_at),
        'total_power': state.total_power,
        'over_temperature': state.over_temperature,
        'error_code': state.error_code,
        'ports': {port.value: _port(reading)
                  for port, reading in state.ports.items()},
        'outputs': {output.value: _output(control)
                    for output, control in state.outputs.items()},
        'settings': _settings(state.settings),
        'energy': {port.value: value for port, value in state.energy.items()},
        'capabilities': capabilities,
    }


def _port(reading: PortReading) -> dict[str, Any]:
    return {'active': reading.active, 'delivering': reading.delivering,
            'status_code': reading.status_code, 'voltage': reading.voltage,
            'current': reading.current, 'power': reading.power,
            'cable': enum_value(reading.cable),
            'charging_protocol': enum_value(reading.charging_protocol),
            'device_brand': enum_value(reading.device_brand),
            'usb_vendor_id': reading.usb_vendor_id,
            'usb_product_id': reading.usb_product_id}


def _output(control: OutputState) -> dict[str, Any]:
    return {'is_on': control.is_on, 'countdown': _countdown(control.countdown),
            'power_on_schedule': _schedule(control.power_on_schedule),
            'power_off_schedule': _schedule(control.power_off_schedule),
            'preferred': control.preferred, 'fault': control.fault}


def _settings(settings: ChargerSettings) -> dict[str, Any]:
    display = settings.display
    clock = settings.clock
    clock_data = None
    if clock is not None:
        clock_data = {'enabled': clock.enabled, 'theme': enum_value(clock.theme),
                      'holiday_theme': clock.holiday_theme,
                      'schedule': _clock_schedule(clock.schedule)}
    return {
        'charging_mode': enum_value(settings.charging_mode),
        'charging_mode_code': settings.charging_mode_code,
        'fixed_allocation': enum_value(settings.fixed_allocation),
        'max_compatibility': settings.max_compatibility,
        'device_identification': settings.device_identification,
        'ufcs': settings.ufcs,
        'updating_firmware': settings.updating_firmware,
        'display': {'brightness': display.brightness,
                    'screen_timeout': enum_value(display.screen_timeout),
                    'orientation': enum_value(display.orientation),
                    'auto_rotation': display.auto_rotation,
                    'language': enum_value(display.language),
                    'knob_direction': enum_value(display.knob_direction),
                    'hour_format': enum_value(display.hour_format)},
        'clock': clock_data,
    }


def _countdown(countdown: Countdown | None) -> dict[str, Any] | None:
    if countdown is None:
        return None
    return {'total': countdown.total, 'remaining': countdown.remaining,
            'ends_at': _time(countdown.ends_at)}


def _schedule(schedule: Schedule | None) -> dict[str, Any] | None:
    if schedule is None:
        return None
    return {'enabled': schedule.enabled,
            'time': f'{schedule.hour:02d}:{schedule.minute:02d}',
            'weekdays': int(schedule.weekdays)}


def _clock_schedule(schedule: ClockSchedule | None) -> dict[str, Any] | None:
    if schedule is None:
        return None
    return {'start': f'{schedule.start_hour:02d}:{schedule.start_minute:02d}',
            'end': f'{schedule.end_hour:02d}:{schedule.end_minute:02d}',
            'weekdays': int(schedule.weekdays)}


def _time(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
