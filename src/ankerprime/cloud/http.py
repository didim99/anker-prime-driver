"""``HttpClient`` on top of an aiohttp session."""

from collections.abc import Mapping

import aiohttp

from ankerprime.interfaces.http import HttpClient, HttpResponse
from ankerprime.model.errors import ChargerConnectionError


DEFAULT_TIMEOUT = 30
UPLOAD_TIMEOUT = 120


class AiohttpClient(HttpClient):
    """Uses a caller-owned session; redirects are refused, not followed.

    Credentials go to a fixed allowlist of hosts, so a redirect could only
    forward them somewhere else.
    """

    _session: aiohttp.ClientSession
    _timeout: aiohttp.ClientTimeout

    def __init__(self, session: aiohttp.ClientSession,
                 timeout: float = DEFAULT_TIMEOUT):
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def post(self, url: str, headers: Mapping[str, str],
                   body: bytes) -> HttpResponse:
        return await self._send('POST', url, headers, body, self._timeout)

    async def put(self, url: str, headers: Mapping[str, str],
                  body: bytes) -> HttpResponse:
        timeout = aiohttp.ClientTimeout(total=max(self._timeout.total or 0,
                                                  UPLOAD_TIMEOUT))
        return await self._send('PUT', url, headers, body, timeout)

    async def _send(self, method: str, url: str, headers: Mapping[str, str],
                    body: bytes, timeout: aiohttp.ClientTimeout
                    ) -> HttpResponse:
        try:
            async with self._session.request(
                    method, url, headers=dict(headers), data=body,
                    allow_redirects=False, timeout=timeout) as response:
                return HttpResponse(response.status, await response.read())
        except (aiohttp.ClientError, TimeoutError) as error:
            message = f'Anker cloud request failed: {error}'
            raise ChargerConnectionError(message) from error
