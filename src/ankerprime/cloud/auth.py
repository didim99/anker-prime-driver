"""Anker account login and the authentication it yields.

The password is used for one request and never kept by this module; whether
an embedder stores it for automatic re-login is its own decision.

Mainland China accounts sign in with a phone number and an SMS code on
their own server (``request_phone_code``, ``login_phone``). Such a login
cannot be repeated without a new code, so it never re-logs in by itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ankerprime.cloud.crypto import LoginCipher
from ankerprime.cloud.regions import (
    CN_SERVER, MAINLAND_COUNTRY, PHONE_REGIONS,
    home_server, known_server, server_for)
from ankerprime.interfaces.http import HttpClient
from ankerprime.interfaces.storage import JsonDocument
from ankerprime.model.errors import (
    AuthenticationError, ChargerConnectionError,
    InvalidParameterError, ProtocolError)
from ankerprime.utils import Clock, parse_json_object, utc_now


LOGIN_PATH = '/passport/login'
PHONE_CODE_PATH = '/passport/phone_verification_code'
PHONE_LOGIN_PATH = '/passport/phone_verification_login'
PHONE_NUMBER = re.compile(r'1[0-9]{10}')
PHONE_CODE = re.compile(r'[0-9]{6}')
# Mainland server messages can echo the input; these codes get own wording.
PHONE_ERRORS = {
    26124: 'this phone number has no Anker account; register it in the '
           'Anker app and bind the charger first',
    26008: 'the SMS code could not be sent; try again later',
    26125: 'the SMS code could not be sent; try again later',
}
# Headers the mainland app sends, on top of the common ones.
MAINLAND_HEADERS = {'model-type': 'PHONE', 'app-version': '3.23.0',
                    'language': 'zh-Hans', 'x-app-key': 'CN'}
DOCUMENT_VERSION = 1
VALIDITY_LEEWAY = timedelta(seconds=60)
MILLISECOND_EPOCH_THRESHOLD = 100_000_000_000


@dataclass(init=False)
class Authentication:
    """Token of a signed-in account and the server that issued it."""

    _user_id: str
    _nickname: str | None
    _auth_token: str
    _expires_at: datetime
    _country: str
    _server: str

    def __init__(self, *, user_id: str, auth_token: str, expires_at: datetime,
                 country: str, server: str, nickname: str | None = None):
        self._user_id = user_id
        self._nickname = nickname
        self._auth_token = auth_token
        self._expires_at = expires_at
        self._country = country.upper()
        self._server = server

    @property
    def user_id(self) -> str:
        """Anker account id; also the owner id the 160W handshake uses."""

        return self._user_id

    @property
    def nickname(self) -> str | None:
        return self._nickname

    @property
    def auth_token(self) -> str:
        return self._auth_token

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @property
    def country(self) -> str:
        return self._country

    @property
    def server(self) -> str:
        return self._server

    def is_valid(self, now: datetime) -> bool:
        """The token exists and does not expire within a minute."""

        return (bool(self._auth_token)
                and self._expires_at - now > VALIDITY_LEEWAY)

    def to_document(self) -> JsonDocument:
        return {'version': DOCUMENT_VERSION, 'user_id': self._user_id,
                'nickname': self._nickname, 'auth_token': self._auth_token,
                'expires_at': int(self._expires_at.timestamp()),
                'country': self._country, 'server': self._server}

    def __repr__(self) -> str:
        return (f'Authentication(user_id={self._user_id!r}, token=***, '
                f'expires_at={self._expires_at.isoformat()}, '
                f'server={self._server!r})')

    @classmethod
    def from_document(cls, document: JsonDocument) -> Authentication | None:
        """Restore a stored token; None when unusable.

        The server is re-checked against the allowlist so a stored document
        can never send the token to another host.
        """

        try:
            server = known_server(document['server'])
            if server is None or document['version'] != DOCUMENT_VERSION:
                return None
            expires_at = datetime.fromtimestamp(document['expires_at'], UTC)
            return cls(user_id=document['user_id'],
                       auth_token=document['auth_token'],
                       expires_at=expires_at, country=document['country'],
                       server=server, nickname=document.get('nickname'))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None


@dataclass(init=False)
class AccountCredentials:
    """What a login needs; kept by embedders that re-login automatically."""

    _email: str
    _password: str
    _country: str

    def __init__(self, email: str, password: str, country: str):
        self._email = email.strip()
        self._password = password
        self._country = country.strip().upper()

    @property
    def email(self) -> str:
        return self._email

    @property
    def password(self) -> str:
        return self._password

    @property
    def country(self) -> str:
        return self._country

    def to_document(self) -> JsonDocument:
        return {'version': DOCUMENT_VERSION, 'email': self._email,
                'password': self._password, 'country': self._country}

    def __repr__(self) -> str:
        return (f'AccountCredentials(email={self._email!r}, password=***, '
                f'country={self._country!r})')

    @classmethod
    def from_document(cls, document: JsonDocument
                      ) -> AccountCredentials | None:
        try:
            if document['version'] != DOCUMENT_VERSION:
                return None
            return cls(document['email'], document['password'],
                       document['country'])
        except (KeyError, TypeError, AttributeError):
            return None


class AccountClient:
    """Signs in with email and password."""

    _http: HttpClient
    _clock: Clock

    def __init__(self, http: HttpClient,
                 clock: Clock | None = None):
        self._http = http
        self._clock = clock or utc_now

    async def login(self, email: str, password: str,
                    country: str) -> Authentication:
        """Sign in and return the token.

        Both overseas servers accept every account, but devices are listed
        only on the account's home server, so a response naming another
        server is followed once.
        """

        email = email.strip()
        country = country.strip().upper()
        _validate_login(email, password, country)

        body = self._login_body(email, password, country)
        server = server_for(country)
        data = await self._post_login(server, body, country)
        home = home_server(data.get('domain'), data.get('ab_code'))
        if home is not None and home != server:
            server = home
            data = await self._post_login(server, body, country)
        return _parse_login(data, country, server)

    async def request_phone_code(self, phone: str) -> None:
        """Have the mainland server send a login code by SMS to the number.

        One request per call, no retry: every call sends a new SMS.
        """

        phone = _checked_phone(phone)
        await self._post_phone(PHONE_CODE_PATH,
                               {'phone_number': phone, 'kind': 'login'})

    async def login_phone(self, phone: str, code: str) -> Authentication:
        """Sign in a mainland China account with the SMS code."""

        phone = _checked_phone(phone)
        code = code.strip()
        if not PHONE_CODE.fullmatch(code):
            raise InvalidParameterError('the SMS code has six digits')
        cipher = LoginCipher()
        data = await self._post_phone(PHONE_LOGIN_PATH, {
            'phone_number': phone, 'verify_code': code,
            'client_secret_info': {'public_key': cipher.public_key_hex}})
        if not isinstance(data, dict):
            raise ProtocolError('login response without data')
        return _parse_login(data, MAINLAND_COUNTRY, CN_SERVER)

    async def _post_phone(self, path: str, body: dict[str, Any]) -> Any:
        headers = base_headers(MAINLAND_COUNTRY, self._clock())
        payload = json.dumps(body).encode('utf-8')
        response = await self._http.post(CN_SERVER + path, headers, payload)
        if not 200 <= response.status < 300:
            raise ChargerConnectionError(f'login HTTP {response.status}')
        root = parse_json_object(response.body)
        code = root.get('code', -1)
        if code != 0:
            message = PHONE_ERRORS.get(code, 'phone login refused')
            raise AuthenticationError(f'{message} ({code})')
        return root.get('data')

    def _login_body(self, email: str, password: str, country: str) -> bytes:
        cipher = LoginCipher()
        now = self._clock()
        offset = now.astimezone().utcoffset() or timedelta()
        body = {'ab': country,
                'client_secret_info': {'public_key': cipher.public_key_hex},
                'enc': 0, 'email': email, 'password': cipher.encrypt(password),
                'time_zone': int(offset.total_seconds() * 1000),
                'transaction': str(int(now.timestamp() * 1000))}
        return json.dumps(body).encode('utf-8')

    async def _post_login(self, server: str, body: bytes,
                          country: str) -> dict[str, Any]:
        headers = base_headers(country, self._clock())
        response = await self._http.post(server + LOGIN_PATH, headers, body)
        if not 200 <= response.status < 300:
            raise ChargerConnectionError(f'login HTTP {response.status}')
        root = parse_json_object(response.body)
        code = root.get('code', -1)
        if code != 0:
            message = root.get('msg') or ''
            raise AuthenticationError(f'login failed ({code}): {message}')
        data = root.get('data')
        if not isinstance(data, dict):
            raise ProtocolError('login response without data')
        return data


def base_headers(country: str, now: datetime) -> dict[str, str]:
    """Headers every Anker API request carries.

    Mainland China requests carry the mainland app's headers as well.
    """

    headers = {'content-type': 'application/json', 'model-type': 'DESKTOP',
               'app-name': 'anker_power', 'os-type': 'android',
               'country': country, 'timezone': gmt_offset(now)}
    if country.upper() == MAINLAND_COUNTRY:
        headers.update(MAINLAND_HEADERS)
        headers['x-auth-ts'] = str(int(now.timestamp()))
    return headers


def gmt_offset(now: datetime) -> str:
    """Local offset as ``GMT+hh:mm``."""

    offset = now.astimezone().utcoffset() or timedelta()
    seconds = int(offset.total_seconds())
    sign = '-' if seconds < 0 else '+'
    hours, rest = divmod(abs(seconds), 3600)
    return f'GMT{sign}{hours:02d}:{rest // 60:02d}'


def parse_expiration(value: object) -> datetime | None:
    """Epoch seconds or milliseconds, as a number or string."""

    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not seconds > 0 or seconds == float('inf'):
        return None
    if seconds > MILLISECOND_EPOCH_THRESHOLD:
        seconds /= 1000
    return datetime.fromtimestamp(seconds, UTC)


def _validate_login(email: str, password: str, country: str) -> None:
    if country in PHONE_REGIONS:
        raise InvalidParameterError('mainland China accounts sign in by '
                                    'phone number and SMS code')
    if '@' not in email or len(email) < 5:
        raise InvalidParameterError('invalid email address')
    if not password:
        raise InvalidParameterError('empty password')


def _checked_phone(phone: str) -> str:
    phone = phone.strip().removeprefix('+86').strip()
    if not PHONE_NUMBER.fullmatch(phone):
        raise InvalidParameterError('a mainland China mobile number has 11 '
                                    'digits and starts with 1')
    return phone


def _parse_login(data: dict[str, Any], country: str,
                 server: str) -> Authentication:
    user_id = data.get('user_id')
    if not isinstance(user_id, str) or not user_id:
        raise ProtocolError('login response without user id')
    token = data.get('auth_token')
    if not isinstance(token, str) or not token:
        raise ProtocolError('login response without auth token')
    expires_at = parse_expiration(data.get('token_expires_at'))
    if expires_at is None:
        raise ProtocolError('login response without token expiry')
    nickname = data.get('nick_name')
    return Authentication(user_id=user_id, auth_token=token,
                          expires_at=expires_at, country=country,
                          server=server,
                          nickname=nickname if isinstance(nickname, str)
                          else None)
