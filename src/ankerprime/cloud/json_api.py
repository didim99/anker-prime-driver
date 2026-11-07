"""One JSON request to an Anker API and the checks every answer gets.

Anker answers ``{"code": 0, "msg": ..., "data": ...}``; a non-zero code is a
refusal. What a refusal or a rejected token means differs per caller (sign-in
versus authenticated requests), so the caller supplies that.
"""

from collections.abc import Callable, Collection, Mapping
from typing import Any

from ankerprime.interfaces.http import HttpClient
from ankerprime.model.errors import (
    AnkerPrimeError, AuthenticationError, ChargerConnectionError)
from ankerprime.utils import parse_json_object


Refusal = Callable[[Any, Any], AnkerPrimeError]
"""Builds the error for a non-zero ``code`` and the server's ``msg``."""

TOKEN_REJECTED = 'Anker token rejected'


async def post_json(http: HttpClient, url: str, headers: Mapping[str, str],
                    body: bytes, *, name: str, refusal: Refusal,
                    auth_statuses: Collection[int] = (),
                    auth_codes: Collection[int] = ()) -> dict[str, Any]:
    """POST a JSON body and return the answer's root object.

    ``name`` labels HTTP errors (``login HTTP 500``). Statuses and codes in
    ``auth_statuses``/``auth_codes`` raise ``AuthenticationError``: the token
    is no longer accepted.
    """

    response = await http.post(url, headers, body)
    if response.status in auth_statuses:
        raise AuthenticationError(TOKEN_REJECTED)
    if not 200 <= response.status < 300:
        raise ChargerConnectionError(f'{name} HTTP {response.status}')
    root = parse_json_object(response.body)
    code = root.get('code')
    if code in auth_codes:
        raise AuthenticationError(TOKEN_REJECTED)
    if code != 0:
        raise refusal(code, root.get('msg'))
    return root
