"""``HttpClient`` on top of an aiohttp session."""

from collections.abc import Mapping

import aiohttp

from ankerprime.interfaces.http import HttpClient, HttpResponse
from ankerprime.model.errors import ChargerConnectionError


DEFAULT_TIMEOUT = 30


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
        try:
            async with self._session.post(url, headers=dict(headers),
                                          data=body, allow_redirects=False,
                                          timeout=self._timeout) as response:
                return HttpResponse(response.status, await response.read())
        except (aiohttp.ClientError, TimeoutError) as error:
            message = f'Anker cloud request failed: {error}'
            raise ChargerConnectionError(message) from error
