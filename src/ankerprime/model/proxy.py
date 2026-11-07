"""An HTTP proxy for the cloud connection: API requests, uploads and MQTT."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

from ankerprime.model.errors import InvalidParameterError


PROXY_SCHEME = 'http'
DEFAULT_PORT = 80


@dataclass(init=False)
class ProxyAddress:
    """An ``http://[user:password@]host:port`` proxy.

    HTTPS and MQTT over TLS go through it with ``CONNECT``, so the proxy
    must allow tunnels to ports 443 and 8883. The password is left out of
    ``repr``.
    """

    _host: str
    _port: int
    _username: str | None
    _password: str | None = field(repr=False)

    def __init__(self, host: str, port: int = DEFAULT_PORT, *,
                 username: str | None = None, password: str | None = None):
        if not host:
            raise InvalidParameterError('proxy host missing')
        if not 0 < port < 0x10000:
            raise InvalidParameterError(f'proxy port out of range: {port}')
        self._host = host
        self._port = port
        self._username = username
        self._password = password

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def password(self) -> str | None:
        return self._password

    @property
    def url(self) -> str:
        """The proxy URL with credentials, for HTTP clients."""

        credentials = ''
        if self._username is not None:
            credentials = quote(self._username, safe='')
            if self._password is not None:
                credentials += ':' + quote(self._password, safe='')
            credentials += '@'
        host = f'[{self._host}]' if ':' in self._host else self._host
        return f'{PROXY_SCHEME}://{credentials}{host}:{self._port}'

    @classmethod
    def parse(cls, text: str) -> ProxyAddress:
        """Read ``http://[user:password@]host[:port]``."""

        try:
            parts = urlsplit(text.strip())
            port = parts.port
        except ValueError as error:
            raise InvalidParameterError(f'invalid proxy URL: {error}'
                                        ) from None
        if parts.scheme != PROXY_SCHEME:
            raise InvalidParameterError(
                'only http:// proxies are supported')
        if parts.path not in ('', '/') or parts.query or parts.fragment:
            raise InvalidParameterError('a proxy URL has no path or query')
        username = (unquote(parts.username)
                    if parts.username is not None else None)
        password = (unquote(parts.password)
                    if parts.password is not None else None)
        return cls(parts.hostname or '', port or DEFAULT_PORT,
                   username=username, password=password)
