"""aiohttp client adapter tests against a local server."""

import base64

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ankerprime.cloud.http import AiohttpClient
from ankerprime.model import ChargerConnectionError
from ankerprime.model.proxy import ProxyAddress
from tests.transport.proxy import running_proxy


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


async def query(request: web.Request) -> web.Response:
    return web.Response(text=request.raw_path)


async def test_put_keeps_a_signed_link_byte_for_byte() -> None:
    app = web.Application()
    app.router.add_put('/upload', query)
    signed = ('/upload?X-Amz-Credential=AKIA%2F20261107%2Fus-east-2'
              '&X-Amz-Security-Token=ab%2Bcd%2Fef%3D%3D&X-Amz-Signature=00')
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        client = AiohttpClient(session)
        url = str(server.make_url('/')).rstrip('/') + signed
        response = await client.put(url, {}, b'x')
    assert response.body.decode() == signed


async def test_requests_go_through_the_proxy() -> None:
    async with running_proxy() as proxy, aiohttp.ClientSession() as session:
        address = ProxyAddress('127.0.0.1', proxy.port, username='u@x',
                               password='p:w')
        client = AiohttpClient(session, proxy=address)
        response = await client.put('http://upload.invalid/a?X-Sig=a%2Fb',
                                    {}, b'x')
    assert response.status == 200
    assert response.body == b'PUT http://upload.invalid/a?X-Sig=a%2Fb HTTP/1.1'
    authorization = proxy.authorizations[0]
    assert authorization is not None
    assert base64.b64decode(authorization.split()[1]) == b'u@x:p:w'
