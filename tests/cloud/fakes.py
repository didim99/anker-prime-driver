"""Test doubles for the cloud layer."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from ankerprime.interfaces.http import HttpClient, HttpResponse


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


def json_response(document: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(document).encode('utf-8'))
