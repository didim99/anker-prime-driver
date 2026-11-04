"""Plain-text rendering of devices and charger states."""

from collections.abc import Sequence
from datetime import datetime

from ankerprime.cloud.client import BoundDevice
from ankerprime.model.enums import Weekday
from ankerprime.model.state import (
    ChargerState, ClockSchedule, OutputState, PortReading, Schedule)
from ankerprime.utils import enum_value


DAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
COLUMN_GAP = '  '
UNKNOWN = '-'


def render_table(header: Sequence[str],
                 rows: Sequence[Sequence[str]]) -> str:
    """Left-aligned columns separated by two spaces."""

    widths = [max(len(row[index]) for row in (header, *rows))
              for index in range(len(header))]
    lines = [COLUMN_GAP.join(cell.ljust(width)
                             for cell, width in zip(row, widths, strict=True))
             .rstrip() for row in (header, *rows)]
    return '\n'.join(lines)


def render_devices(devices: Sequence[BoundDevice]) -> str:
    if not devices:
        return 'No devices are bound to this account.'
    rows = [(device.serial, device.product_code, device.display_name,
             _flag(device.wifi_online, 'online', 'offline'),
             device.software_version or UNKNOWN)
            for device in devices]
    return render_table(('Serial', 'Model', 'Name', 'Wi-Fi', 'Firmware'), rows)


def render_state(state: ChargerState, now: datetime) -> str:
    """Multi-line summary of everything the charger reported.

    Times are shown in the time zone of ``now``.
    """

    sections = [_header(state, now), _ports(state), _outputs(state, now),
                _settings(state)]
    return '\n\n'.join(section for section in sections if section)


def render_readings(state: ChargerState, now: datetime) -> str:
    """The header and the port table only."""

    sections = [_header(state, now), _ports(state)]
    return '\n\n'.join(section for section in sections if section)


def format_days(days: Weekday) -> str:
    if days == Weekday.ONCE:
        return 'once'
    if days == Weekday.EVERY_DAY:
        return 'daily'
    return ','.join(label for index, label in enumerate(DAY_LABELS)
                    if days & (1 << index))


def format_age(seconds: float) -> str:
    """Compact elapsed time: ``5 s``, ``2:05``, ``1:02:05``."""

    if seconds < 60:
        return f'{max(int(seconds), 0)} s'
    return format_duration(seconds)


def format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes}:{seconds:02d}'


def _header(state: ChargerState, now: datetime) -> str:
    info = state.info
    name = info.name or state.product.name
    parts = [f'{name} ({state.product.code})']
    if info.firmware_version:
        parts.append(f'firmware {info.firmware_version}')
    parts.append('live' if state.available else 'NOT LIVE')
    lines = [' · '.join(parts), _updated(state.updated_at, now),
             f'Total power {state.total_power:.2f} W']
    if state.over_temperature:
        lines.append('WARNING: over-temperature protection active')
    if state.energy:
        lines[-1] += f' · energy {state.total_energy:.3f} Wh'
    return '\n'.join(lines)


def _updated(updated_at: datetime | None, now: datetime) -> str:
    if updated_at is None:
        return 'Updated never'
    local = updated_at.astimezone(now.tzinfo)
    age = format_age((now - updated_at).total_seconds())
    return f'Updated {local:%H:%M:%S} ({age} ago)'


def _ports(state: ChargerState) -> str:
    if not state.ports:
        return ''
    rows = [_port_row(state, reading) for reading in state.ports.values()]
    header = ('Port', 'Output', 'Status', 'Voltage', 'Current', 'Power',
              'Details')
    return render_table(header, rows)


def _port_row(state: ChargerState, reading: PortReading) -> tuple[str, ...]:
    output = state.product.output_of(reading.port)
    control = state.outputs.get(output)
    switched = _flag(control.is_on if control else None, 'on', 'off')
    status = 'charging' if reading.delivering else (
        'active' if reading.active else 'idle')
    details = [value for value in (enum_value(reading.cable),
                                   enum_value(reading.charging_protocol),
                                   enum_value(reading.device_brand)) if value]
    if reading.usb_vendor_id is not None:
        details.append(f'usb {reading.usb_vendor_id:04x}:'
                       f'{reading.usb_product_id or 0:04x}')
    return (reading.port.value.upper(), switched, status,
            f'{reading.voltage:.2f} V', f'{reading.current:.2f} A',
            f'{reading.power:.2f} W', ', '.join(details))


def _outputs(state: ChargerState, now: datetime) -> str:
    lines = [_output_line(control, now) for control in state.outputs.values()]
    lines = [line for line in lines if line]
    return 'Outputs\n' + '\n'.join(lines) if lines else ''


def _output_line(control: OutputState, now: datetime) -> str:
    notes = []
    if control.countdown is not None:
        left = (control.countdown.ends_at - now).total_seconds()
        notes.append(f'off in {format_duration(left)}')
    for label, schedule in (('on', control.power_on_schedule),
                            ('off', control.power_off_schedule)):
        if schedule is not None:
            notes.append(_schedule(label, schedule))
    if control.preferred:
        notes.append('preferred')
    if control.fault:
        notes.append('FAULT')
    if not notes:
        return ''
    return f'  {control.output.value.upper()}: ' + '; '.join(notes)


def _schedule(label: str, schedule: Schedule) -> str:
    state = '' if schedule.enabled else ' (disabled)'
    return (f'{label} {schedule.hour:02d}:{schedule.minute:02d} '
            f'{format_days(schedule.weekdays)}{state}')


def _settings(state: ChargerState) -> str:
    settings = state.settings
    display = settings.display
    lines = []
    mode = enum_value(settings.charging_mode)
    if mode is None and settings.charging_mode_code is not None:
        mode = f'code {settings.charging_mode_code}'
    lines.append(f'Mode: {mode or UNKNOWN}')
    display_parts = [
        f'brightness {_number(display.brightness, "%")}',
        f'timeout {enum_value(display.screen_timeout) or UNKNOWN}',
    ]
    for label, value in (('knob', display.knob_direction),
                         ('hours', display.hour_format),
                         ('orientation', display.orientation),
                         ('language', display.language)):
        if value is not None:
            display_parts.append(f'{label} {value.value}')
    if display.auto_rotation is not None:
        display_parts.append(
            'auto-rotation ' + _flag(display.auto_rotation, 'on', 'off'))
    lines.append('Display: ' + ', '.join(display_parts))
    clock = settings.clock
    if clock is not None:
        parts = [_flag(clock.enabled, 'on', 'off'),
                 enum_value(clock.theme) or UNKNOWN,
                 'holiday ' + _flag(clock.holiday_theme, 'on', 'off')]
        if clock.schedule is not None:
            parts.append(_clock_schedule(clock.schedule))
        lines.append('Clock: ' + ', '.join(parts))
    switches = [(label, value) for label, value in (
        ('max compatibility', settings.max_compatibility),
        ('device identification', settings.device_identification),
        ('UFCS', settings.ufcs)) if value is not None]
    if switches:
        lines.append('Switches: ' + ', '.join(
            f'{label} {_flag(value, "on", "off")}'
            for label, value in switches))
    if settings.updating_firmware:
        lines.append('Firmware update in progress')
    return '\n'.join(lines)


def _clock_schedule(schedule: ClockSchedule) -> str:
    return (f'{schedule.start_hour:02d}:{schedule.start_minute:02d}-'
            f'{schedule.end_hour:02d}:{schedule.end_minute:02d} '
            f'{format_days(schedule.weekdays)}')


def _flag(value: bool | None, true: str, false: str) -> str:
    if value is None:
        return UNKNOWN
    return true if value else false


def _number(value: int | None, unit: str) -> str:
    return f'{value}{unit}' if value is not None else UNKNOWN
