"""Authenticated Anker cloud API: bound devices and MQTT credentials."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ankerprime.cloud.auth import Authentication, base_headers
from ankerprime.interfaces.http import HttpClient
from ankerprime.model.errors import (
    AuthenticationError, ChargerConnectionError, ProtocolError)
from ankerprime.utils import Clock, parse_json_object


BOUND_DEVICES_PATH = 'power_service/v1/app/get_relate_and_bind_devices'
MQTT_INFO_PATH = 'app/devicemanage/get_user_mqtt_info'

# Anker reports revoked or expired sessions as HTTP or API codes.
AUTH_FAILURE_STATUSES = frozenset({401, 403})
AUTH_FAILURE_CODES = frozenset({401, 403, 26084})

CERTIFICATE_LABELS = ('CERTIFICATE',)
PRIVATE_KEY_LABELS = ('RSA PRIVATE KEY', 'PRIVATE KEY')
PEM_LINE_LENGTH = 64

TOPIC_COMPONENT = re.compile(r'[A-Za-z0-9_-]+')
MQTT_HOST = re.compile(r'[A-Za-z0-9.-]+')


@dataclass(init=False)
class BoundDevice:
    """A charger bound to the account in the Anker app."""

    _serial: str
    _product_code: str
    _device_name: str | None
    _alias_name: str | None
    _wifi_online: bool | None
    _software_version: str | None
    _owner_user_id: str | None

    def __init__(self, serial: str, product_code: str, *,
                 device_name: str | None = None,
                 alias_name: str | None = None,
                 wifi_online: bool | None = None,
                 software_version: str | None = None,
                 owner_user_id: str | None = None):
        self._serial = serial
        self._product_code = product_code
        self._device_name = device_name
        self._alias_name = alias_name
        self._wifi_online = wifi_online
        self._software_version = software_version
        self._owner_user_id = owner_user_id

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def product_code(self) -> str:
        return self._product_code

    @property
    def device_name(self) -> str | None:
        return self._device_name

    @property
    def alias_name(self) -> str | None:
        return self._alias_name

    @property
    def wifi_online(self) -> bool | None:
        return self._wifi_online

    @property
    def software_version(self) -> str | None:
        return self._software_version

    @property
    def owner_user_id(self) -> str | None:
        """Account the charger is bound to; differs for shared devices."""

        return self._owner_user_id

    @property
    def display_name(self) -> str:
        return self._alias_name or self._device_name or self._product_code

    @classmethod
    def from_json(cls, item: Mapping[str, Any]) -> BoundDevice | None:
        serial = item.get('device_sn')
        product_code = item.get('product_code')
        if not isinstance(serial, str) or not isinstance(product_code, str):
            return None
        return cls(serial, product_code,
                   device_name=_optional_str(item, 'device_name'),
                   alias_name=_optional_str(item, 'alias_name'),
                   wifi_online=_optional_bool(item, 'wifi_online'),
                   software_version=_optional_str(item, 'device_sw_version'),
                   owner_user_id=_optional_str(item, 'owner_user_id'))


@dataclass(init=False)
class MqttCredentials:
    """Per-account AWS IoT client identity for the cloud MQTT broker.

    The certificate and key are kept as normalised PEM text because Python's
    ``ssl`` loads client identities from files only.
    """

    _user_id: str
    _app_name: str
    _thing_name: str
    _certificate_id: str
    _endpoint: str
    _certificate_pem: str
    _private_key_pem: str

    def __init__(self, *, user_id: str, app_name: str, thing_name: str,
                 certificate_id: str, endpoint: str, certificate_pem: str,
                 private_key_pem: str):
        self._user_id = user_id
        self._app_name = app_name
        self._thing_name = thing_name
        self._certificate_id = certificate_id
        self._endpoint = endpoint
        self._certificate_pem = certificate_pem
        self._private_key_pem = private_key_pem

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def thing_name(self) -> str:
        return self._thing_name

    @property
    def certificate_id(self) -> str:
        return self._certificate_id

    @property
    def endpoint(self) -> str:
        """Broker host name."""

        return self._endpoint

    @property
    def certificate_pem(self) -> str:
        return self._certificate_pem

    @property
    def private_key_pem(self) -> str:
        return self._private_key_pem

    def client_id(self, suffix: int) -> str:
        """MQTT client id: thing name plus a five-digit random suffix."""

        return f'{self._thing_name}_{suffix % 100_000:05d}'

    def subscription_topic(self, device: BoundDevice) -> str:
        app, product, serial = self._topic_components(device)
        return f'dt/{app}/{product}/{serial}/#'

    def command_topic(self, device: BoundDevice) -> str:
        app, product, serial = self._topic_components(device)
        return f'cmd/{app}/{product}/{serial}/req'

    def _topic_components(self, device: BoundDevice) -> tuple[str, str, str]:
        components = (self._app_name, device.product_code, device.serial)
        for component in components:
            if not TOPIC_COMPONENT.fullmatch(component):
                raise ProtocolError('invalid MQTT topic component')
        return components

    def __repr__(self) -> str:
        return (f'MqttCredentials(endpoint={self._endpoint!r}, '
                f'thing_name={self._thing_name!r}, key=***)')


class CloudClient:
    """Authenticated requests to the account's home server."""

    _http: HttpClient
    _authentication: Authentication
    _clock: Clock

    def __init__(self, http: HttpClient, authentication: Authentication,
                 clock: Clock):
        self._http = http
        self._authentication = authentication
        self._clock = clock

    @property
    def authentication(self) -> Authentication:
        return self._authentication

    async def bound_devices(self, product_code: str | None = None
                            ) -> list[BoundDevice]:
        """Chargers bound to or shared with the account, optionally filtered."""

        data = await self._post(BOUND_DEVICES_PATH)
        items = data.get('data')
        if not isinstance(items, list):
            raise ProtocolError('device list missing from the response')
        devices = [device for device in map(BoundDevice.from_json, items)
                   if device is not None]
        if product_code is None:
            return devices
        return [device for device in devices
                if device.product_code.upper() == product_code.upper()]

    async def mqtt_credentials(self) -> MqttCredentials:
        data = await self._post(MQTT_INFO_PATH)
        fields = {key: data.get(key) for key in (
            'user_id', 'app_name', 'thing_name', 'certificate_id',
            'endpoint_addr', 'certificate_pem', 'private_key')}
        if not all(isinstance(value, str) and value
                   for value in fields.values()):
            raise ProtocolError('incomplete MQTT credentials')
        endpoint = str(fields['endpoint_addr'])
        certificate = normalize_pem(str(fields['certificate_pem']),
                                    CERTIFICATE_LABELS)
        private_key = normalize_pem(str(fields['private_key']),
                                    PRIVATE_KEY_LABELS)
        valid_host = MQTT_HOST.fullmatch(endpoint) is not None
        if not valid_host or not certificate or not private_key:
            raise ProtocolError('invalid MQTT credentials')
        return MqttCredentials(user_id=str(fields['user_id']),
                               app_name=str(fields['app_name']),
                               thing_name=str(fields['thing_name']),
                               certificate_id=str(fields['certificate_id']),
                               endpoint=endpoint, certificate_pem=certificate,
                               private_key_pem=private_key)

    async def request(self, path: str, body: Mapping[str, Any] | None = None,
                      headers: Mapping[str, str] | None = None) -> Any:
        """POST a JSON body to an API path; return the response's ``data``.

        ``data`` may be any JSON value, None when the response has none.
        """

        authentication = self._authentication
        now = self._clock()
        if not authentication.is_valid(now):
            raise AuthenticationError('Anker token expired')
        request_headers = base_headers(authentication.country, now)
        request_headers['x-auth-token'] = authentication.auth_token
        request_headers['gtoken'] = hashlib.md5(
            authentication.user_id.encode('utf-8')).hexdigest()
        request_headers.update(headers or {})
        url = f'{authentication.server}/{_checked_path(path)}'
        payload = json.dumps(dict(body or {}),
                             separators=(',', ':')).encode('utf-8')
        response = await self._http.post(url, request_headers, payload)
        if response.status in AUTH_FAILURE_STATUSES:
            raise AuthenticationError('Anker token rejected')
        if not 200 <= response.status < 300:
            raise ChargerConnectionError(f'cloud HTTP {response.status}')
        root = parse_json_object(response.body)
        code = root.get('code')
        if code in AUTH_FAILURE_CODES:
            raise AuthenticationError('Anker token rejected')
        if code != 0:
            raise ProtocolError(f'Anker cloud error {code}: '
                                f'{root.get("msg") or ""}')
        return root.get('data')

    async def _post(self, path: str) -> dict[str, Any]:
        data = await self.request(path)
        if not isinstance(data, dict):
            raise ProtocolError('response without data')
        return data


def normalize_pem(value: str, labels: tuple[str, ...]) -> str | None:
    """Validate one PEM block and re-wrap it; None when malformed.

    Text outside the block, missing boundaries and empty bodies are rejected.
    """

    for label in labels:
        header = f'-----BEGIN {label}-----'
        footer = f'-----END {label}-----'
        start = value.find(header)
        end = value.find(footer)
        if start < 0 or end < start:
            continue
        if value[:start].strip() or value[end + len(footer):].strip():
            continue
        encoded = ''.join(value[start + len(header):end].split())
        try:
            der = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not der:
            continue
        body = base64.b64encode(der).decode('ascii')
        lines = [body[index:index + PEM_LINE_LENGTH]
                 for index in range(0, len(body), PEM_LINE_LENGTH)]
        return '\n'.join([header, *lines, footer]) + '\n'
    return None


def _checked_path(path: str) -> str:
    if (not path or path.startswith('/') or '..' in path or '://' in path
            or '?' in path or '#' in path):
        raise ProtocolError(f'invalid API path: {path}')
    return path


def _optional_str(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _optional_bool(item: Mapping[str, Any], key: str) -> bool | None:
    value = item.get(key)
    return value if isinstance(value, bool) else None
