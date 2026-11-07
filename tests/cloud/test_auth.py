"""Account login tests against a recording HTTP fake."""

from datetime import UTC, datetime, timedelta

import pytest

from ankerprime.cloud.auth import (
    AccountClient, AccountCredentials, Authentication, gmt_offset)
from ankerprime.cloud.regions import CN_SERVER, COM_SERVER, EU_SERVER
from ankerprime.cloud.values import parse_expiration
from ankerprime.interfaces.http import HttpResponse
from ankerprime.model import (
    AuthenticationError, ChargerConnectionError,
    InvalidParameterError, ProtocolError)
from tests.cloud.fakes import FakeHttpClient, Request, json_response
from tests.utils import make_authentication


NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
EXPIRY = 2_000_000_000
USER_ID = 'e' * 40


def login_data(**extra: object) -> dict[str, object]:
    data: dict[str, object] = {'user_id': USER_ID, 'auth_token': 'test-token',
                               'token_expires_at': EXPIRY}
    data.update(extra)
    return {'code': 0, 'msg': 'success', 'data': data}


def client_for(http: FakeHttpClient) -> AccountClient:
    return AccountClient(http, lambda: NOW)


async def test_login_posts_encrypted_password_and_returns_token() -> None:
    http = FakeHttpClient(lambda _: json_response(login_data(
        nick_name='tester')))
    auth = await client_for(http).login(' person@example.com ',
                                        'do-not-send-in-clear', 'jp')
    request = http.requests[0]
    assert request.url == EU_SERVER + '/passport/login'
    assert request.headers['country'] == 'JP'
    assert request.headers['app-name'] == 'anker_power'
    assert request.headers['model-type'] == 'DESKTOP'
    body = request.json()
    assert body['ab'] == 'JP' and body['email'] == 'person@example.com'
    assert body['enc'] == 0
    assert len(bytes.fromhex(body['client_secret_info']['public_key'])) == 65
    assert b'do-not-send-in-clear' not in request.body
    assert body['transaction'] == str(int(NOW.timestamp() * 1000))

    assert auth.user_id == USER_ID and auth.nickname == 'tester'
    assert auth.auth_token == 'test-token'
    assert auth.expires_at == datetime.fromtimestamp(EXPIRY, UTC)
    assert auth.country == 'JP' and auth.server == EU_SERVER


async def test_login_follows_named_domain_once() -> None:
    def handler(request: Request) -> HttpResponse:
        return json_response(login_data(
            auth_token=f'token-from-{request.host}',
            domain='ankerpower-api-eu.anker.com'))

    http = FakeHttpClient(handler)
    auth = await client_for(http).login('a@example.com', 'test', 'US')
    assert [request.host for request in http.requests] == [
        'ankerpower-api.anker.com', 'ankerpower-api-eu.anker.com']
    assert auth.auth_token == 'token-from-ankerpower-api-eu.anker.com'
    assert auth.server == EU_SERVER and auth.country == 'US'


async def test_login_follows_account_country() -> None:
    http = FakeHttpClient(lambda _: json_response(login_data(
        domain='', ab_code='JP')))
    auth = await client_for(http).login('a@example.com', 'test', 'US')
    assert len(http.requests) == 2
    assert auth.server == EU_SERVER


@pytest.mark.parametrize('code', ['CN', 'ZZ', ''])
async def test_mainland_or_unknown_account_country_keeps_route(
        code: str) -> None:
    http = FakeHttpClient(lambda _: json_response(login_data(ab_code=code)))
    auth = await client_for(http).login('a@example.com', 'test', 'US')
    assert [request.host for request in http.requests] == [
        'ankerpower-api.anker.com']
    assert auth.server == COM_SERVER


async def test_login_ignores_domain_outside_allowlist() -> None:
    domain = 'https://ankerpower-api-eu.anker.com.example.org'
    http = FakeHttpClient(lambda _: json_response(login_data(domain=domain)))
    auth = await client_for(http).login('a@example.com', 'test', 'JP')
    assert len(http.requests) == 1
    assert auth.server == EU_SERVER


@pytest.mark.parametrize(('email', 'password', 'country'), [
    ('nope', 'x', 'JP'), ('a@b.com', '', 'JP'), ('a@b.com', 'x', 'CN'),
])
async def test_input_is_validated_before_network(
        email: str, password: str, country: str) -> None:
    http = FakeHttpClient(lambda _: json_response(login_data()))
    with pytest.raises(InvalidParameterError):
        await client_for(http).login(email, password, country)
    assert not http.requests


async def test_rejected_login_raises_authentication_error() -> None:
    http = FakeHttpClient(lambda _: json_response(
        {'code': 100053, 'msg': 'wrong password'}))
    with pytest.raises(AuthenticationError, match='wrong password'):
        await client_for(http).login('a@example.com', 'bad', 'JP')


async def test_http_failure_raises_connection_error() -> None:
    http = FakeHttpClient(lambda _: HttpResponse(502, b''))
    with pytest.raises(ChargerConnectionError):
        await client_for(http).login('a@example.com', 'x', 'JP')


@pytest.mark.parametrize('data', [
    {'auth_token': 'test-token', 'token_expires_at': EXPIRY},
    {'user_id': USER_ID, 'token_expires_at': EXPIRY},
    {'user_id': USER_ID, 'auth_token': 'test-token'},
])
async def test_incomplete_login_response_fails_closed(
        data: dict[str, object]) -> None:
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': data}))
    with pytest.raises(ProtocolError):
        await client_for(http).login('a@example.com', 'x', 'JP')


async def test_malformed_response() -> None:
    http = FakeHttpClient(lambda _: HttpResponse(200, b'<html>'))
    with pytest.raises(ProtocolError):
        await client_for(http).login('a@example.com', 'x', 'JP')


@pytest.mark.parametrize(('raw', 'expected'), [
    (EXPIRY, EXPIRY), (str(EXPIRY), EXPIRY), (EXPIRY * 1000, EXPIRY),
    (0, None), (-5, None), ('soon', None), (None, None),
])
def test_parse_expiration(raw: object, expected: int | None) -> None:
    parsed = parse_expiration(raw)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == datetime.fromtimestamp(expected, UTC)


def test_gmt_offset_format() -> None:
    text = gmt_offset(NOW)
    assert text.startswith('GMT') and len(text) == 9 and text[6] == ':'


def make_tester() -> Authentication:
    return make_authentication(USER_ID, country='jp', nickname='tester')


def test_authentication_validity() -> None:
    auth = make_tester()
    assert auth.is_valid(NOW)
    assert not auth.is_valid(auth.expires_at - timedelta(seconds=30))


def test_authentication_document_round_trip() -> None:
    auth = make_tester()
    document = auth.to_document()
    assert 'password' not in document
    assert Authentication.from_document(document) == auth


@pytest.mark.parametrize('change', [
    {'server': 'https://example.org'}, {'version': 99}, {'expires_at': 'x'},
    {'user_id': None},
])
def test_tampered_authentication_document_is_rejected(
        change: dict[str, object]) -> None:
    document = make_tester().to_document()
    document.update(change)
    if change == {'user_id': None}:
        del document['user_id']
    assert Authentication.from_document(document) is None


def test_reprs_hide_secrets() -> None:
    assert 'test-token' not in repr(make_tester())
    credentials = AccountCredentials(' a@b.com ', 'secret', 'jp')
    assert 'secret' not in repr(credentials)
    assert (credentials.email, credentials.country) == ('a@b.com', 'JP')


def test_account_credentials_document_round_trip() -> None:
    credentials = AccountCredentials('a@b.com', 'secret', 'JP')
    restored = AccountCredentials.from_document(credentials.to_document())
    assert restored == credentials
    assert AccountCredentials.from_document({'version': 1}) is None


async def test_phone_code_request() -> None:
    http = FakeHttpClient(lambda _: json_response({'code': 0, 'data': None}))
    await client_for(http).request_phone_code(' +86 13800138000 ')
    request = http.requests[0]
    assert request.url == CN_SERVER + '/passport/phone_verification_code'
    assert request.json() == {'phone_number': '13800138000', 'kind': 'login'}
    headers = request.headers
    assert headers['country'] == 'CN' and headers['model-type'] == 'PHONE'
    assert headers['language'] == 'zh-Hans' and headers['x-app-key'] == 'CN'
    assert headers['app-version'] == '3.23.0'
    assert headers['x-auth-ts'] == str(int(NOW.timestamp()))


async def test_phone_login_returns_a_mainland_token() -> None:
    http = FakeHttpClient(lambda _: json_response(login_data()))
    auth = await client_for(http).login_phone('13800138000', ' 123456 ')
    request = http.requests[0]
    assert request.url == CN_SERVER + '/passport/phone_verification_login'
    body = request.json()
    assert body['phone_number'] == '13800138000'
    assert body['verify_code'] == '123456'
    assert len(bytes.fromhex(body['client_secret_info']['public_key'])) == 65
    assert auth.country == 'CN' and auth.server == CN_SERVER
    assert auth.user_id == USER_ID
    restored = Authentication.from_document(auth.to_document())
    assert restored == auth


@pytest.mark.parametrize('phone, code', [
    ('23800138000', '123456'), ('1380013800', '123456'),
    ('13800138000', '12345'), ('13800138000', 'abcdef')])
async def test_phone_login_checks_input(phone: str, code: str) -> None:
    http = FakeHttpClient(lambda _: json_response(login_data()))
    with pytest.raises(InvalidParameterError):
        await client_for(http).login_phone(phone, code)
    assert http.requests == []


@pytest.mark.parametrize('code, message', [
    (26124, 'no Anker account'), (26008, 'could not be sent'),
    (10001, 'phone login refused')])
async def test_phone_errors_do_not_echo_the_server(code: int,
                                                   message: str) -> None:
    http = FakeHttpClient(lambda _: json_response(
        {'code': code, 'msg': 'number 13800138000 unknown'}))
    with pytest.raises(AuthenticationError, match=message) as info:
        await client_for(http).request_phone_code('13800138000')
    assert '13800138000' not in str(info.value)


async def test_email_login_points_mainland_accounts_to_the_phone() -> None:
    http = FakeHttpClient(lambda _: json_response(login_data()))
    with pytest.raises(InvalidParameterError, match='phone'):
        await client_for(http).login('a@example.com', 'secret', 'CN')


def test_stored_token_allows_only_known_servers() -> None:
    auth = make_authentication(USER_ID, country='CN')
    document = auth.to_document()
    document['server'] = CN_SERVER + '/'
    restored = Authentication.from_document(document)
    assert restored is not None and restored.server == CN_SERVER
    document['server'] = 'https://aiot-api-cn.example.com'
    assert Authentication.from_document(document) is None
