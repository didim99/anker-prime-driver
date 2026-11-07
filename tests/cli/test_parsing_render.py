"""CLI argument converters, rendering and state serialisation tests."""

import argparse
import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from ankerprime.cli.parsing import (
    enum_converter, parse_countdown, parse_days,
    parse_duration, parse_output, parse_switch, parse_time)
from ankerprime.cli.render import (
    format_age, format_days, format_duration, render_devices,
    render_readings, render_runtime, render_state, render_table)
from ankerprime.cli.terminal import TerminalBlock
from ankerprime.cloud.client import BoundDevice
from ankerprime.model import (
    A2345, ChargingMode, OutputId, PortId, ScreenTimeout, Weekday)
from ankerprime.model.serialize import state_to_dict
from ankerprime.protocol.a2345.frame import decode_frame
from ankerprime.protocol.a2345.messages import decode_message
from ankerprime.protocol.a2345.settings import CountdownUpdate, SettingsChange
from ankerprime.protocol.a2345.tracker import StateTracker
from tests.protocol.a2345.fixtures import (
    REALTIME_CHINA_FIRMWARE, STATUS_SNAPSHOT)


NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def snapshot_state() -> StateTracker:
    tracker = StateTracker()
    for data in (STATUS_SNAPSHOT, REALTIME_CHINA_FIRMWARE):
        tracker.apply(decode_message(decode_frame(data)), NOW)
    return tracker


@pytest.mark.parametrize(('text', 'expected'), [
    ('c1', OutputId.C1), ('C4', OutputId.C4), ('usb_a', OutputId.USB_A),
    ('a', OutputId.USB_A), ('USB-A', OutputId.USB_A),
])
def test_parse_output(text: str, expected: OutputId) -> None:
    assert parse_output(text) == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('90', 90), ('15m', 900), ('2h', 7200), ('1h30m', 5400), ('45s', 45),
])
def test_parse_duration(text: str, expected: int) -> None:
    assert parse_duration(text) == expected
    assert parse_countdown(text) == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('daily', Weekday.EVERY_DAY), ('once', Weekday.ONCE),
    ('mon,fri', Weekday.MONDAY | Weekday.FRIDAY),
    ('Monday,Sunday', Weekday.MONDAY | Weekday.SUNDAY),
    ('weekend', Weekday.SATURDAY | Weekday.SUNDAY),
])
def test_parse_days(text: str, expected: Weekday) -> None:
    assert parse_days(text) == expected


@pytest.mark.parametrize(('converter', 'text'), [
    (parse_output, 'c9'), (parse_switch, 'maybe'), (parse_time, '24:00'),
    (parse_time, '7'), (parse_days, 'funday'), (parse_duration, 'soon'),
    (parse_duration, ''), (enum_converter(ScreenTimeout), '2min'),
])
def test_invalid_arguments(converter: object, text: str) -> None:
    assert callable(converter)
    with pytest.raises(argparse.ArgumentTypeError):
        converter(text)


def test_simple_converters() -> None:
    assert parse_countdown('Cancel') is None
    assert parse_switch('ON') is True and parse_switch('off') is False
    assert parse_time('07:05') == (7, 5)
    converter = enum_converter(ChargingMode)
    assert converter('Port-Priority') == ChargingMode.PORT_PRIORITY
    assert converter.__name__ == 'ChargingMode'


@pytest.mark.parametrize(('days', 'text'), [
    (Weekday.ONCE, 'once'), (Weekday.EVERY_DAY, 'daily'),
    (Weekday.MONDAY | Weekday.WEDNESDAY, 'Mon,Wed'),
])
def test_format_days(days: Weekday, text: str) -> None:
    assert format_days(days) == text


@pytest.mark.parametrize(('seconds', 'text'), [
    (59, '0:59'), (600, '10:00'), (3725, '1:02:05'), (-3, '0:00'),
    (2 * 86400 + 3725, '2d 1:02:05'),
])
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


@pytest.mark.parametrize(('seconds', 'text'), [
    (0.4, '0 s'), (59.9, '59 s'), (125, '2:05'), (3725, '1:02:05'),
])
def test_format_age(seconds: float, text: str) -> None:
    assert format_age(seconds) == text


def test_render_runtime() -> None:
    text = render_runtime(NOW, NOW + timedelta(hours=26, seconds=5))
    assert text == 'Running since 2026-11-04 12:00:00 (1d 2:00:05)'


def test_render_table_aligns_columns() -> None:
    table = render_table(('A', 'Long header'), [('wide cell', 'x')])
    assert table.splitlines() == ['A          Long header',
                                  'wide cell  x']


def test_render_devices() -> None:
    text = render_devices([BoundDevice('SERIAL123', 'A2345',
                                       alias_name='Desk', wifi_online=True,
                                       software_version='2.1.1.6')])
    assert 'SERIAL123' in text and 'Desk' in text and 'online' in text
    assert render_devices([]).startswith('No devices')


def test_render_state() -> None:
    tracker = snapshot_state()
    tracker.apply(SettingsChange(0x0307, [CountdownUpdate(OutputId.C2, True,
                                                          900, 600)]), NOW)
    text = render_state(tracker.build(available=True),
                        NOW + timedelta(seconds=60))
    assert 'Anker Prime Charger 250W (A2345)' in text
    assert 'firmware 2.1.1.2 · live' in text
    assert 'Updated 12:00:00 (1:00 ago)' in text
    assert 'C1    off     charging' in text
    assert '5a, huawei_scp, huawei_honor' in text
    assert 'C2: off in 9:00' in text
    assert 'C4: on 08:00 Tue (disabled); off 23:00 Tue (disabled)' in text
    assert 'Mode: custom' in text
    assert 'brightness 20%' in text and 'timeout 30s' in text
    assert 'Clock: off, style_2, holiday off, 08:00-23:00 daily' in text
    assert 'Switches: max compatibility off' in text


def test_render_readings_shows_ports_only() -> None:
    tracker = snapshot_state()
    state = tracker.build(available=False, energy={PortId.C1: 1.234,
                                                   PortId.C2: 0.5})
    text = render_readings(state, NOW + timedelta(seconds=5))
    assert 'NOT LIVE' in text and 'Updated 12:00:00 (5 s ago)' in text
    assert 'Total power 23.94 W · energy 1.73 Wh' in text
    assert 'C1    off     charging' in text
    assert '23.84 W  1.23 Wh' in text and '0.00 W   -' in text
    assert 'Outputs' not in text and 'Mode:' not in text
    assert render_readings(StateTracker().build(available=False),
                           NOW).splitlines()[1] == 'Updated never'


def test_terminal_block_replaces_its_lines() -> None:
    out = io.StringIO()
    with TerminalBlock(out) as block:
        block.show('one\ntwo')
        block.show('three')
    assert out.getvalue() == ('\x1b[?25l\x1b[Jone\ntwo\n'
                              '\x1b[2F\x1b[Jthree\n\x1b[?25h')


def test_state_to_dict_is_json_ready() -> None:
    document = state_to_dict(snapshot_state().build(available=True))
    text = json.dumps(document)
    restored = json.loads(text)
    assert restored['product']['code'] == 'A2345'
    assert restored['ports']['c1']['cable'] == '5a'
    assert restored['outputs']['c4']['power_on_schedule'] == {
        'enabled': False, 'time': '08:00', 'weekdays': 2}
    assert restored['settings']['display']['screen_timeout'] == '30s'
    assert restored['settings']['clock']['schedule']['end'] == '23:00'
    assert restored['settings']['clock']['picture_id'] is None
    assert restored['capabilities']['output_switch'] == 'verified'
    assert restored['settings']['active_profile'] == 1
    assert restored['settings']['custom_profiles'] is None


def test_profiles_in_the_json_state() -> None:
    assert A2345.custom_mode is not None
    profile = A2345.custom_mode.template(1, 'Desk')
    state = snapshot_state().build(available=True, profiles=[profile])
    document = json.loads(json.dumps(state_to_dict(state)))
    stored = document['settings']['custom_profiles'][0]
    assert stored['name'] == 'Desk' and stored['total_watts'] == 250
    assert stored['ports']['usb_a'] == {'watts': 15, 'protocols': ['ufcs']}
