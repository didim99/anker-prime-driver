"""A minimal HTTP proxy for tests: tunnels ``CONNECT``, echoes the rest."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class TestProxy:
    """Records each request line; answers plain requests with that line."""

    __test__ = False

    _server: asyncio.Server | None
    _requests: list[str]
    _authorizations: list[str | None]

    def __init__(self) -> None:
        self._server = None
        self._requests = []
        self._authorizations = []

    @property
    def port(self) -> int:
        assert self._server is not None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    @property
    def requests(self) -> list[str]:
        return self._requests

    @property
    def authorizations(self) -> list[str | None]:
        """``Proxy-Authorization`` of each request."""

        return self._authorizations

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, '127.0.0.1', 0)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode().strip()
            authorization = None
            while (header := await reader.readline()) not in (b'\r\n', b''):
                name, _, value = header.decode().partition(':')
                if name.strip().lower() == 'proxy-authorization':
                    authorization = value.strip()
            self._requests.append(line)
            self._authorizations.append(authorization)
            method, target, _ = line.split(' ', 2)
            if method == 'CONNECT':
                await self._tunnel(target, reader, writer)
                return
            body = line.encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '
                         + str(len(body)).encode()
                         + b'\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _tunnel(self, target: str, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        host, _, port = target.rpartition(':')
        upstream_reader, upstream_writer = await asyncio.open_connection(
            host, int(port))
        writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        await writer.drain()
        await asyncio.gather(_pipe(reader, upstream_writer),
                             _pipe(upstream_reader, writer))


async def _pipe(reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


@asynccontextmanager
async def running_proxy() -> AsyncIterator[TestProxy]:
    proxy = TestProxy()
    await proxy.start()
    try:
        yield proxy
    finally:
        await proxy.stop()
