"""Text rendering of devices and charger states, optionally coloured.

Every function takes a ``Painter``; the default ``PLAIN`` one adds nothing.
"""

from collections.abc import Sequence
from datetime import datetime

from ankerprime.cli.style import PLAIN, Painter, visible_width
from ankerprime.cloud.client import BoundDevice
from ankerprime.model.enums import Weekday
from ankerprime.model.state import (
    ChargerState, ClockSchedule, OutputState, PortReading, Schedule)
from ankerprime.utils import enum_value


DAY_LABELS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
COLUMN_GAP = '  '
UNKNOWN = '-'


def render_table(header: Sequence[str], rows: Sequence[Sequence[str]], *,
                 painter: Painter = PLAIN) -> str:
    """Left-aligned columns separated by two spaces; cells may be coloured."""

    widths = [max(visible_width(row[index]) for row in (header, *rows))
              for index in range(len(header))]
    painted = [tuple(painter.heading(cell) for cell in header), *rows]
    lines = [COLUMN_GAP.join(_pad(cell, width)
                             for cell, width in zip(row, widths, strict=True))
             .rstrip() for row in painted]
    return '\n'.join(lines)


def render_devices(devices: Sequence[BoundDevice], *,
                   painter: Painter = PLAIN) -> str:
    if not devices:
        return 'No devices are bound to this account.'
    rows = [(painter.accent(device.serial), device.product_code,
             device.display_name,
             _switch(painter, device.wifi_online, 'online', 'offline',
                     off_bad=True),
             device.software_version or UNKNOWN)
            for device in devices]
    return render_table(('Serial', 'Model', 'Name', 'Wi-Fi', 'Firmware'), rows,
                        painter=painter)


def render_state(state: ChargerState, now: datetime, *,
                 painter: Painter = PLAIN) -> str:
    """Multi-line summary of everything the charger reported.

    Times are shown in the time zone of ``now``.
    """

    sections = [_header(state, now, painter), _ports(state, painter),
                _outputs(state, now, painter), _settings(state, painter)]
    return '\n\n'.join(section for section in sections if section)


def render_readings(state: ChargerState, now: datetime, *,
                    painter: Painter = PLAIN) -> str:
    """The header and the port table only."""

    sections = [_header(state, now, painter), _ports(state, painter)]
    return '\n\n'.join(section for section in sections if section)


def format_days(days: Weekday) -> str:
    if days == Weekday.ONCE:
        return 'once'
    if days == Weekday.EVERY_DAY:
        return 'daily'
    return ','.join(label for index, label in enumerate(DAY_LABELS)
                    if days & (1 << index))


def render_runtime(started: datetime, now: datetime, *,
                   painter: Painter = PLAIN) -> str:
    """How long a command has been running, in the time zone of ``now``."""

    local = started.astimezone(now.tzinfo)
    elapsed = format_duration((now - started).total_seconds())
    return painter.dim(f'Running since {local:%Y-%m-%d %H:%M:%S} ({elapsed})')


def format_age(seconds: float) -> str:
    """Compact elapsed time: ``5 s``, ``2:05``, ``1:02:05``."""

    if seconds < 60:
        return f'{max(int(seconds), 0)} s'
    return format_duration(seconds)


def format_duration(seconds: float) -> str:
    """``0:59``, ``1:02:05``, ``2d 1:02:05``."""

    seconds = max(int(seconds), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f'{days}d {hours}:{minutes:02d}:{seconds:02d}'
    if hours:
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes}:{seconds:02d}'


def _header(state: ChargerState, now: datetime, painter: Painter) -> str:
    info = state.info
    name = info.name or state.product.name
    parts = [painter.strong(f'{name} ({state.product.code})')]
    if info.firmware_version:
        parts.append(f'firmware {info.firmware_version}')
    parts.append(painter.good('live') if state.available
                 else painter.bad('NOT LIVE'))
    total = f'Total power {painter.strong(f"{state.total_power:.2f} W")}'
    if state.energy:
        total += f' · energy {painter.strong(f"{state.total_energy:.2f} Wh")}'
    lines = [' · '.join(parts),
             _updated(state.updated_at, now, state.available, painter), total]
    if state.over_temperature:
        lines.append(painter.bad('WARNING: over-temperature protection '
                                 'active'))
    return '\n'.join(lines)


def _updated(updated_at: datetime | None, now: datetime, available: bool,
             painter: Painter) -> str:
    if updated_at is None:
        return painter.warn('Updated never')
    local = updated_at.astimezone(now.tzinfo)
    age = format_age((now - updated_at).total_seconds())
    text = f'Updated {local:%H:%M:%S} ({age} ago)'
    return painter.dim(text) if available else painter.warn(text)


def _ports(state: ChargerState, painter: Painter) -> str:
    if not state.ports:
        return ''
    rows = [_port_row(state, reading, painter)
            for reading in state.ports.values()]
    header = ('Port', 'Output', 'Status', 'Voltage', 'Current', 'Power',
              'Energy', 'Details')
    return render_table(header, rows, painter=painter)


def _port_row(state: ChargerState, reading: PortReading,
              painter: Painter) -> tuple[str, ...]:
    output = state.product.output_of(reading.port)
    control = state.outputs.get(output)
    switched = _switch(painter, control.is_on if control else None)
    if reading.delivering:
        status = painter.good('charging')
    elif reading.active:
        status = 'active'
    else:
        status = painter.dim('idle')
    details = [value for value in (enum_value(reading.cable),
                                   enum_value(reading.charging_protocol),
                                   enum_value(reading.device_brand)) if value]
    if reading.usb_vendor_id is not None:
        details.append(f'usb {reading.usb_vendor_id:04x}:'
                       f'{reading.usb_product_id or 0:04x}')
    measure = painter.strong if reading.power > 0 else painter.dim
    energy = state.energy.get(reading.port)
    energy_text = UNKNOWN if energy is None else f'{energy:.2f} Wh'
    return (painter.accent(reading.port.value.upper()), switched, status,
            measure(f'{reading.voltage:.2f} V'),
            measure(f'{reading.current:.2f} A'),
            measure(f'{reading.power:.2f} W'),
            energy_text if energy else painter.dim(energy_text),
            painter.dim(', '.join(details)))


def _outputs(state: ChargerState, now: datetime, painter: Painter) -> str:
    lines = [_output_line(control, now, painter)
             for control in state.outputs.values()]
    lines = [line for line in lines if line]
    if not lines:
        return ''
    return painter.heading('Outputs') + '\n' + '\n'.join(lines)


def _output_line(control: OutputState, now: datetime,
                 painter: Painter) -> str:
    notes = []
    if control.countdown is not None:
        left = (control.countdown.ends_at - now).total_seconds()
        notes.append(painter.warn(f'off in {format_duration(left)}'))
    for label, schedule in (('on', control.power_on_schedule),
                            ('off', control.power_off_schedule)):
        if schedule is not None:
            notes.append(_schedule(label, schedule, painter))
    if control.preferred:
        notes.append(painter.good('preferred'))
    if control.fault:
        notes.append(painter.bad('FAULT'))
    if not notes:
        return ''
    label = painter.accent(control.output.value.upper())
    return f'  {label}: ' + '; '.join(notes)


def _schedule(label: str, schedule: Schedule, painter: Painter) -> str:
    text = (f'{label} {schedule.hour:02d}:{schedule.minute:02d} '
            f'{format_days(schedule.weekdays)}')
    return text if schedule.enabled else painter.dim(text + ' (disabled)')


def _settings(state: ChargerState, painter: Painter) -> str:
    settings = state.settings
    display = settings.display
    lines = []
    mode = enum_value(settings.charging_mode)
    if mode is None and settings.charging_mode_code is not None:
        mode = f'code {settings.charging_mode_code}'
    mode_parts = [painter.strong(mode or UNKNOWN)]
    if settings.fixed_allocation is not None:
        mode_parts.append(f'allocation {settings.fixed_allocation.value}')
    if settings.active_profile is not None:
        profile = settings.custom_profile(settings.active_profile)
        name = f' "{profile.name}"' if profile is not None else ''
        mode_parts.append(f'profile {settings.active_profile}{name}')
    if settings.custom_mode_enabled is not None:
        mode_parts.append('custom mode setting '
                          + _switch(painter, settings.custom_mode_enabled))
    lines.append(_line(painter, 'Mode', mode_parts))
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
            'auto-rotation ' + _switch(painter, display.auto_rotation))
    lines.append(_line(painter, 'Display', display_parts))
    clock = settings.clock
    if clock is not None:
        theme = enum_value(clock.theme) or UNKNOWN
        if clock.picture_id is not None:
            theme += f' {clock.picture_id}'
        parts = [_switch(painter, clock.enabled), theme,
                 'holiday ' + _switch(painter, clock.holiday_theme)]
        if clock.picture_time is not None:
            parts.append('time over own pictures '
                         + _switch(painter, clock.picture_time))
        if clock.schedule is not None:
            parts.append(_clock_schedule(clock.schedule))
        lines.append(_line(painter, 'Clock', parts))
    switches = [(label, value) for label, value in (
        ('max compatibility', settings.max_compatibility),
        ('device identification', settings.device_identification),
        ('UFCS', settings.ufcs)) if value is not None]
    if switches:
        lines.append(_line(painter, 'Switches', [
            f'{label} {_switch(painter, value)}'
            for label, value in switches]))
    if settings.updating_firmware:
        lines.append(painter.warn('Firmware update in progress'))
    return '\n'.join(lines)


def _line(painter: Painter, label: str, parts: Sequence[str]) -> str:
    return painter.strong(f'{label}:') + ' ' + ', '.join(parts)


def _clock_schedule(schedule: ClockSchedule) -> str:
    return (f'{schedule.start_hour:02d}:{schedule.start_minute:02d}-'
            f'{schedule.end_hour:02d}:{schedule.end_minute:02d} '
            f'{format_days(schedule.weekdays)}')


def _switch(painter: Painter, value: bool | None, true: str = 'on',
            false: str = 'off', *, off_bad: bool = False) -> str:
    if value is None:
        return painter.dim(UNKNOWN)
    if value:
        return painter.good(true)
    return painter.bad(false) if off_bad else painter.dim(false)


def _pad(cell: str, width: int) -> str:
    return cell + ' ' * (width - visible_width(cell))


def _number(value: int | None, unit: str) -> str:
    return f'{value}{unit}' if value is not None else UNKNOWN
