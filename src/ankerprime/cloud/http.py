"""``HttpClient`` on top of an aiohttp session."""

from collections.abc import Mapping

import aiohttp
from yarl import URL

from ankerprime.interfaces.http import HttpClient, HttpResponse
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.model.proxy import ProxyAddress


DEFAULT_TIMEOUT = 30
UPLOAD_TIMEOUT = 120


class AiohttpClient(HttpClient):
    """Uses a caller-owned session; redirects are refused, not followed.

    Credentials go to a fixed allowlist of hosts, so a redirect could only
    forward them somewhere else. With a ``proxy`` every request goes
    through it.
    """

    _session: aiohttp.ClientSession
    _timeout: aiohttp.ClientTimeout
    _proxy: ProxyAddress | None

    def __init__(self, session: aiohttp.ClientSession,
                 timeout: float = DEFAULT_TIMEOUT, *,
                 proxy: ProxyAddress | None = None):
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._proxy = proxy

    async def post(self, url: str, headers: Mapping[str, str],
                   body: bytes) -> HttpResponse:
        return await self._send('POST', url, headers, body, self._timeout)

    async def put(self, url: str, headers: Mapping[str, str],
                  body: bytes) -> HttpResponse:
        """PUT to a signed link, sent exactly as given (no re-quoting)."""

        timeout = aiohttp.ClientTimeout(total=max(self._timeout.total or 0,
                                                  UPLOAD_TIMEOUT))
        return await self._send('PUT', url, headers, body, timeout)

    async def _send(self, method: str, url: str, headers: Mapping[str, str],
                    body: bytes, timeout: aiohttp.ClientTimeout
                    ) -> HttpResponse:
        # A signed link is valid only byte for byte; yarl would otherwise
        # normalize its percent-escapes.
        target = URL(url, encoded=True)
        proxy = self._proxy.url if self._proxy is not None else None
        try:
            async with self._session.request(
                    method, target, headers=dict(headers), data=body,
                    allow_redirects=False, timeout=timeout,
                    proxy=proxy) as response:
                return HttpResponse(response.status, await response.read())
        except (aiohttp.ClientError, TimeoutError) as error:
            message = f'Anker cloud request failed: {error}'
            raise ChargerConnectionError(message) from error
