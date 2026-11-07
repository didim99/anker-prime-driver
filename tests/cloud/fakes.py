"""Test doubles for the cloud layer."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from ankerprime.interfaces.http import HttpClient, HttpResponse


DEVICES = [
    {'device_sn': 'SERIAL123', 'product_code': 'A2345',
     'alias_name': 'Desk charger', 'device_sw_version': '2.1.1.6'},
    {'device_sn': 'SERIAL456', 'product_code': 'A2687'},
]
MQTT_INFO = {
    'user_id': 'u' * 40, 'app_name': 'anker_power', 'thing_name': 'thing',
    'certificate_id': 'certificate', 'endpoint_addr': 'aiot-mqtt-eu.anker.com',
    'certificate_pem': '-----BEGIN CERTIFICATE-----\nAQID\n'
                       '-----END CERTIFICATE-----',
    'private_key': '-----BEGIN PRIVATE KEY-----\nBAUG\n'
                   '-----END PRIVATE KEY-----',
}


class Request:
    """One recorded request."""

    _url: str
    _headers: dict[str, str]
    _body: bytes

    def __init__(self, url: str, headers: Mapping[str, str], body: bytes):
        self._url = url
        self._headers = dict(headers)
        self._body = body

    @property
    def url(self) -> str:
        return self._url

    @property
    def headers(self) -> dict[str, str]:
        return self._headers

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def host(self) -> str:
        return self._url.split('/')[2]

    @property
    def path(self) -> str:
        return '/' + self._url.split('/', 3)[3]

    def json(self) -> Any:
        return json.loads(self._body)


Handler = Callable[[Request], HttpResponse]


class FakeHttpClient(HttpClient):
    """Records requests and answers them with a handler."""

    _handler: Handler
    _requests: list[Request]

    def __init__(self, handler: Handler):
        self._handler = handler
        self._requests = []

    @property
    def requests(self) -> list[Request]:
        return self._requests

    async def post(self, url: str, headers: Mapping[str, str],
                   body: bytes) -> HttpResponse:
        request = Request(url, headers, body)
        self._requests.append(request)
        return self._handler(request)


class FakeMiniPower:
    """The account's charger settings and custom profiles (``mini_power``)."""

    _settings: dict[str, dict[str, int]]
    _profiles: dict[int, dict[str, Any]]
    _next_id: int
    _calls: list[tuple[str, dict[str, Any]]]

    PREFIX = '/mini_power/v1/app/'

    def __init__(self) -> None:
        self._settings = {}
        self._profiles = {}
        self._next_id = 100
        self._calls = []

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """Endpoint name and body of every request, in order."""

        return self._calls

    @property
    def profiles(self) -> dict[int, dict[str, Any]]:
        return self._profiles

    def settings(self, serial: str) -> dict[str, int]:
        return self._settings.setdefault(serial, {
            'charging_mode_status': 0, 'compatibility_status': 0,
            'charging_device_identity_status_default_true': 1})

    def handles(self, request: Request) -> bool:
        return request.path.startswith(self.PREFIX)

    def __call__(self, request: Request) -> HttpResponse:
        name = request.path.rsplit('/', 1)[1]
        body = request.json()
        self._calls.append((name, body))
        data: Any = None
        if name == 'get_device_setting':
            values = self.settings(body['device_sn'])
            data = {'device_setting': {
                'charging_mode_status': values['charging_mode_status'],
                'compatibility_status': values['compatibility_status'],
                'charging_device_identity_status': 0}}
        elif name == 'get_charging_device_identity_status_default_true':
            key = 'charging_device_identity_status_default_true'
            data = {key: self.settings(body['device_sn'])[key]}
        elif name.startswith('set_'):
            switches = {key: value for key, value in body.items()
                        if key != 'device_sn'}
            self.settings(body['device_sn']).update(switches)
        elif name == 'get_charging_mode_list':
            data = {'charging_mode_list': [
                profile for profile in self._profiles.values()
                if profile['device_sn'] == body['device_sn']]}
        elif name == 'add_charging_mode':
            self._next_id += 1
            self._profiles[self._next_id] = {**body, 'id': self._next_id}
        elif name == 'update_charging_mode':
            stored = self._profiles[body['id']]
            stored.update(body)
        elif name == 'delete_charging_mode':
            del self._profiles[body['id']]
        else:
            return HttpResponse(404, b'')
        return json_response({'code': 0, 'msg': 'ok', 'data': data})


class FakeAnkerCloud:
    """Answers the Anker API; tokens can be revoked."""

    _devices: list[dict[str, Any]]
    _tokens: list[str]
    _revoked: set[str]
    _login_ok: bool
    _mini_power: FakeMiniPower

    def __init__(self, devices: list[dict[str, Any]] | None = None):
        self._devices = DEVICES if devices is None else devices
        self._tokens = []
        self._revoked = set()
        self._login_ok = True
        self._mini_power = FakeMiniPower()

    @property
    def mini_power(self) -> FakeMiniPower:
        return self._mini_power

    @property
    def tokens(self) -> list[str]:
        return self._tokens

    def revoke(self, token: str) -> None:
        self._revoked.add(token)

    def set_login_ok(self, ok: bool) -> None:
        self._login_ok = ok

    def __call__(self, request: Request) -> HttpResponse:
        if request.path == '/passport/login':
            return self._login()
        if request.headers.get('x-auth-token') in self._revoked:
            return json_response({'code': 26084, 'msg': 'expired'})
        if request.path.endswith('get_relate_and_bind_devices'):
            return json_response({'code': 0, 'data': {'data': self._devices}})
        if request.path.endswith('get_user_mqtt_info'):
            return json_response({'code': 0, 'data': MQTT_INFO})
        if self._mini_power.handles(request):
            return self._mini_power(request)
        return HttpResponse(404, b'')

    def _login(self) -> HttpResponse:
        if not self._login_ok:
            return json_response({'code': 100053, 'msg': 'wrong password'})
        token = f'token-{len(self._tokens) + 1}'
        self._tokens.append(token)
        return json_response({'code': 0, 'data': {
            'user_id': 'u' * 40, 'auth_token': token,
            'token_expires_at': 2_000_000_000}})


def json_response(document: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(document).encode('utf-8'))
