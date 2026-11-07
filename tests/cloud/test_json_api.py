"""The shared JSON request and its error mapping."""

from typing import Any

import pytest

from ankerprime.cloud.json_api import post_json
from ankerprime.interfaces.http import HttpResponse
from ankerprime.model import (
    AnkerPrimeError, AuthenticationError, ChargerConnectionError,
    ProtocolError)
from tests.cloud.fakes import FakeHttpClient, json_response


def refusal(code: Any, message: Any) -> AnkerPrimeError:
    return ProtocolError(f'refused {code}: {message}')


async def post(response: HttpResponse) -> dict[str, Any]:
    http = FakeHttpClient(lambda _: response)
    return await post_json(http, 'https://api/x', {'h': '1'}, b'{}',
                           name='test', refusal=refusal,
                           auth_statuses={401}, auth_codes={26084})


async def test_answer_is_returned() -> None:
    root = await post(json_response({'code': 0, 'data': {'a': 1}}))
    assert root['data'] == {'a': 1}


@pytest.mark.parametrize('response, error, message', [
    (HttpResponse(401, b''), AuthenticationError, 'token rejected'),
    (HttpResponse(500, b''), ChargerConnectionError, 'test HTTP 500'),
    (json_response({'code': 26084}), AuthenticationError, 'token rejected'),
    (json_response({'code': 7, 'msg': 'no'}), ProtocolError, 'refused 7: no'),
    (HttpResponse(200, b'[]'), ProtocolError, 'unexpected JSON'),
])
async def test_failures(response: HttpResponse, error: type[Exception],
                        message: str) -> None:
    with pytest.raises(error, match=message):
        await post(response)
