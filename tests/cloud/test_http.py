"""aiohttp client adapter tests against a local server."""

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ankerprime.cloud.http import AiohttpClient
from ankerprime.model import ChargerConnectionError


async def echo(request: web.Request) -> web.Response:
    body = await request.read()
    return web.Response(status=201, body=body,
                        headers={'x-seen': request.headers['x-test']})


async def redirect(request: web.Request) -> web.Response:
    raise web.HTTPFound('/echo')


async def test_post_returns_status_and_body() -> None:
    app = web.Application()
    app.router.add_post('/echo', echo)
    app.router.add_post('/redirect', redirect)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = AiohttpClient(session)
        response = await client.post(str(server.make_url('/echo')),
                                     {'x-test': '1'}, b'{"a": 1}')
        assert (response.status, response.body) == (201, b'{"a": 1}')
        response = await client.post(str(server.make_url('/redirect')),
                                     {'x-test': '1'}, b'')
        assert response.status == 302


async def test_network_failure_raises_connection_error() -> None:
    async with aiohttp.ClientSession() as session:
        client = AiohttpClient(session, timeout=2)
        with pytest.raises(ChargerConnectionError):
            await client.post('http://127.0.0.1:9/', {}, b'')


async def test_put_sends_the_body() -> None:
    app = web.Application()
    app.router.add_put('/upload', echo)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = AiohttpClient(session)
        response = await client.put(str(server.make_url('/upload')),
                                    {'x-test': '1'}, b'\xff\xd8jpeg')
        assert (response.status, response.body) == (201, b'\xff\xd8jpeg')
