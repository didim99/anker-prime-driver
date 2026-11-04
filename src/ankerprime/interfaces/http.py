"""Minimal HTTP client used by the cloud layer.

Embedders that already own an HTTP session (Home Assistant's shared aiohttp
session) pass it in through an implementation of this interface; tests use a
recording fake.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(init=False)
class HttpResponse:
    """Status and raw body of one response."""

    _status: int
    _body: bytes

    def __init__(self, status: int, body: bytes):
        self._status = status
        self._body = body

    @property
    def status(self) -> int:
        return self._status

    @property
    def body(self) -> bytes:
        return self._body


class HttpClient(ABC):
    """Sends requests without following redirects."""

    @abstractmethod
    async def post(self, url: str, headers: Mapping[str, str],
                   body: bytes) -> HttpResponse:
        """Send a POST; raises ``ChargerConnectionError`` on network failure."""

        pass
