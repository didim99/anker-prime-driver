"""Account-side charger settings and custom profiles (``mini_power``)."""

import zlib
from datetime import UTC, datetime

import pytest

from ankerprime.cloud.client import CloudClient
from ankerprime.cloud.mini_power import (
    MiniPowerClient, decode_picture, decode_profile,
    flexible_bool, flexible_int, hex_u32, prefixed_u32)
from ankerprime.interfaces.http import HttpResponse
from ankerprime.model import (
    A2345, ChargerConnectionError, CustomModeRules,
    CustomPortLimit, CustomProtocol, OutputId, ProtocolError)
from tests.cloud.fakes import (
    FakeHttpClient, FakeMiniPower, Request, json_response)
from tests.utils import make_authentication


NOW = datetime.fromtimestamp(1_900_000_000, UTC)
SERIAL = 'SERIAL123'


def rules() -> CustomModeRules:
    assert A2345.custom_mode is not None
    return A2345.custom_mode


def make_client(server: FakeMiniPower | None = None, country: str = 'DE'
                ) -> tuple[MiniPowerClient, FakeHttpClient]:
    http = FakeHttpClient(server or FakeMiniPower())
    authentication = make_authentication('u' * 40, country=country)
    client = CloudClient(http, authentication, lambda: NOW)
    return MiniPowerClient(client, SERIAL), http


async def test_settings_read_both_endpoints() -> None:
    server = FakeMiniPower()
    server.settings(SERIAL).update({'charging_mode_status': 1,
                                    'compatibility_status': 0})
    client, http = make_client(server)
    settings = await client.settings()
    assert settings.custom_mode_enabled is True
    assert settings.max_compatibility is False
    assert settings.device_identification is True
    assert http.requests[0].json() == {'device_sn': SERIAL}
    assert http.requests[0].headers['content-type'] == 'application/json'


async def test_flat_settings_and_missing_identification() -> None:
    def handler(request: Request) -> HttpResponse:
        if request.path.endswith('get_device_setting'):
            return json_response({'code': 0, 'data': {
                'charging_mode_status': '1', 'compatibility_status': 1}})
        return json_response({'code': 10000, 'msg': 'unknown'})

    http = FakeHttpClient(handler)
    client = MiniPowerClient(
        CloudClient(http, make_authentication('u' * 40), lambda: NOW), SERIAL)
    settings = await client.settings()
    assert settings.custom_mode_enabled is True
    assert settings.max_compatibility is True
    assert settings.device_identification is None


async def test_switches_send_zero_or_one() -> None:
    server = FakeMiniPower()
    client, _ = make_client(server)
    await client.set_custom_mode_enabled(True)
    await client.set_max_compatibility(True)
    await client.set_device_identification(False)
    assert server.calls == [
        ('set_charging_mode_status',
         {'device_sn': SERIAL, 'charging_mode_status': 1}),
        ('set_compatibility_status',
         {'device_sn': SERIAL, 'compatibility_status': 1}),
        ('set_charging_device_identity_status_default_true',
         {'device_sn': SERIAL,
          'charging_device_identity_status_default_true': 0}),
    ]


async def test_profile_round_trip() -> None:
    server = FakeMiniPower()
    client, _ = make_client(server)
    profile = rules().template(2, 'Desk')
    await client.add_profile(profile, rules())
    name, body = server.calls[-1]
    assert name == 'add_charging_mode'
    assert body['device_sn'] == SERIAL and body['number'] == 2
    assert body['total_power'] == 250 and body['max_total_power'] == 250
    assert body['has_charge_protocol'] == 1 and body['auto_exit'] == 0
    c1 = body['power_settings'][0]
    assert c1['name'] == 'C1' and c1['power'] == 100
    assert c1['max_power'] == 140
    assert c1['pps20v'] == 1 and c1['pd12v'] == 0
    assert body['power_settings'][4]['name'] == 'A'

    stored = await client.profiles()
    assert len(stored) == 1
    loaded = stored[0]
    assert loaded.profile_id is not None
    assert loaded.number == 2 and loaded.name == 'Desk'
    assert [port.watts for port in loaded.ports] == [100, 65, 45, 25, 15]
    assert loaded.port(OutputId.C1) == profile.port(OutputId.C1)

    changed = loaded.with_port(CustomPortLimit(OutputId.C1, 60))
    await client.update_profile(changed, rules())
    name, body = server.calls[-1]
    assert name == 'update_charging_mode'
    assert body['id'] == loaded.profile_id and 'device_sn' not in body
    assert 'number' not in body
    assert (await client.profiles())[0].watts(OutputId.C1) == 60

    await client.delete_profile(loaded.profile_id)
    assert await client.profiles() == []


async def test_update_needs_an_account_id() -> None:
    client, _ = make_client()
    with pytest.raises(ProtocolError):
        await client.update_profile(rules().template(1, 'P'), rules())


async def test_mainland_limits_in_the_body() -> None:
    server = FakeMiniPower()
    client, _ = make_client(server, country='CN')
    assert client.mainland
    await client.add_profile(rules().template(1, 'P', mainland=True),
                             rules())
    body = server.calls[-1][1]
    assert body['power_settings'][1]['max_power'] == 120


async def test_profile_list_as_bare_list() -> None:
    items = [{'id': '7', 'number': 3, 'name': 'B', 'power_settings': []},
             {'id': 8, 'number': 1, 'name': 'A', 'power_settings': []},
             {'name': 'no number'}]
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': items}))
    client = MiniPowerClient(
        CloudClient(http, make_authentication('u' * 40), lambda: NOW), SERIAL)
    profiles = await client.profiles()
    assert [profile.number for profile in profiles] == [1, 3]
    assert profiles[1].profile_id == 7


def test_decode_profile_by_position_and_flexible_values() -> None:
    profile = decode_profile({
        'id': 5, 'number': '2', 'name': 'X', 'auto_exit': True,
        'has_charge_protocol': '1',
        'power_settings': [{'power': '30', 'scp': '1', 'pps20v': True},
                           {'name': 'a', 'power': 15}],
    })
    assert profile is not None
    assert profile.auto_exit and profile.protocols_enabled
    c1 = profile.port(OutputId.C1)
    assert c1 is not None and c1.watts == 30
    assert c1.protocols == {CustomProtocol.SCP, CustomProtocol.PPS_21V}
    assert profile.watts(OutputId.USB_A) == 15


@pytest.mark.parametrize('value, expected', [
    (1, 1), ('2', 2), (True, 1), (3.0, 3), (2.5, None), ('x', None),
    (None, None),
])
def test_flexible_int(value: object, expected: int | None) -> None:
    assert flexible_int(value) == expected


def test_flexible_bool() -> None:
    assert flexible_bool('1') is True
    assert flexible_bool(0) is False
    assert flexible_bool(None) is None


async def test_clock_picture_catalogue() -> None:
    server = FakeMiniPower()
    client, http = make_client(server)
    pictures = await client.clock_pictures('zh')
    request = http.requests[0]
    assert request.json() == {'product_code': 'A2345'}
    assert request.headers['Language'] == 'zh-Hans'
    assert [picture.picture_id for picture in pictures] == [31, 32, 34]
    forest, sea, night = pictures
    assert forest.title == 'Forest' and forest.category == 'Nature'
    assert forest.crc32 == 0x1A2B3C4D and forest.text_color == 0xFF00FF00
    assert sea.title == 'Sea' and sea.image_url == 'https://cdn/sea.jpg'
    assert sea.crc32 == 0xBEEF and sea.text_color is None
    assert night.crc32 == 12345 and night.category == 'City'
    assert not forest.own
    await client.clock_pictures('fr')
    assert http.requests[1].headers['Language'] == 'en'


async def test_own_picture_upload_list_delete() -> None:
    server = FakeMiniPower()
    client, http = make_client(server)
    jpeg = b'\xff\xd8\xff' + b'picture' * 10
    await client.upload_own_picture(jpeg)
    ticket = http.requests[0]
    assert ticket.path == '/app/cloudstor/get_app_up_token_general'
    assert ticket.headers['user_id'] == 'u' * 40
    assert ticket.json() == {'file_name': 'cropped_image.jpg',
                             'content_type': 'binary/octet-stream',
                             'file_size': len(jpeg), 'disable_ssl': False,
                             'type': 106}
    upload = http.requests[1]
    assert upload.method == 'PUT' and upload.body == jpeg
    assert upload.headers['content-type'] == 'binary/octet-stream'
    assert upload.headers['content-length'] == str(len(jpeg))
    name, body = server.calls[-1]
    assert name == 'add_manual_clock_screensavers'
    assert body == {'sn': SERIAL, 'img_url': 'pictures/key1',
                    'hash_code': f'0x{zlib.crc32(jpeg):08x}'}

    own = await client.own_pictures()
    assert len(own) == 1
    picture = own[0]
    assert picture.own and picture.crc32 == zlib.crc32(jpeg)
    assert picture.image_url.startswith('https://own/pictures/key1')
    await client.delete_own_picture(picture.picture_id)
    assert await client.own_pictures() == []


async def test_upload_link_must_be_https() -> None:
    def handler(request: Request) -> HttpResponse:
        return json_response({'code': 0, 'data': {
            'uptoken': 'http://plain/upload', 'key_prefix': 'k'}})

    http = FakeHttpClient(handler)
    client = MiniPowerClient(
        CloudClient(http, make_authentication('u' * 40), lambda: NOW), SERIAL)
    with pytest.raises(ProtocolError, match='HTTPS'):
        await client.upload_own_picture(b'\xff\xd8\xff')
    assert len(http.requests) == 1


async def test_failed_upload_stops_before_registering() -> None:
    def handler(request: Request) -> HttpResponse:
        if request.method == 'PUT':
            return HttpResponse(403, b'')
        return json_response({'code': 0, 'data': {
            'uptoken': 'https://upload/x', 'key_prefix': 'k'}})

    http = FakeHttpClient(handler)
    client = MiniPowerClient(
        CloudClient(http, make_authentication('u' * 40), lambda: NOW), SERIAL)
    with pytest.raises(ChargerConnectionError, match='403'):
        await client.upload_own_picture(b'\xff\xd8\xff')
    assert len(http.requests) == 2


@pytest.mark.parametrize('value, expected', [
    ('0x1A', 0x1A), ('1a', 0x1A), (26, 26), ('zz', None), (True, None),
])
def test_hex_u32(value: object, expected: int | None) -> None:
    assert hex_u32(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('0x10', 16), ('10', 10), (7, 7), ('x', None), (None, None),
])
def test_prefixed_u32(value: object, expected: int | None) -> None:
    assert prefixed_u32(value) == expected


def test_decode_picture_needs_id_and_link() -> None:
    assert decode_picture({'image_url': 'https://x'}) is None
    assert decode_picture({'id': 1}) is None
    picture = decode_picture({'id': 1, 'img_url': 'https://x'}, own=True)
    assert picture is not None and picture.own and picture.crc32 == 0
