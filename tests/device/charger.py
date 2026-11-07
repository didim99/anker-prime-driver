"""Frame builders and a channel that plays the charger's side."""

import asyncio
from collections.abc import Callable

from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.protocol.a2345.frame import Frame, decode_frame
from ankerprime.protocol.ff09 import HEADER, checksum
from tests.protocol.a2345.fixtures import (
    REALTIME_OLD_FIRMWARE, STATUS_SNAPSHOT, THEME_STATUS)


Responder = Callable[[Frame], list[bytes]]

ACK_MARKER = b'\x34'
GROUP_ECHO_ACKS = (0x0A07, 0x0A08, 0x0A09)
IMAGE_TIME_DISPLAY = 0x0222
TAG_IMAGE_TIME_DISPLAY = 0xA6


def charger_frame(message_type: int, fields: list[tuple[int, bytes]],
                  increment: int | None = None) -> bytes:
    """Frame as the charger sends it (speaker 1)."""

    body = bytearray(HEADER + b'\x00\x00' + bytes((3, 1, 15)))
    body += message_type.to_bytes(2, 'big')
    if increment is not None:
        body.append(increment)
    for tag, raw in fields:
        body += bytes((tag, len(raw))) + raw
    body[2:4] = (len(body) + 1).to_bytes(2, 'little')
    body.append(checksum(body))
    return bytes(body)


def theme_status(image_time_display: bool) -> bytes:
    """The captured ``0x0A02`` with the time over own pictures set."""

    frame = decode_frame(THEME_STATUS)
    fields = [(field.tag, field.raw) for field in frame.fields
              if field.tag != TAG_IMAGE_TIME_DISPLAY]
    fields.append((TAG_IMAGE_TIME_DISPLAY,
                   bytes((0x01, int(image_time_display)))))
    return charger_frame(frame.message_type, fields, frame.increment)


def ack_for(request: Frame, status: int = 0) -> bytes:
    """Acknowledgement answering a write, echoing group and slot."""

    ack_type = 0x0A00 | (request.message_type & 0xFF)
    fields = [(0xA1, ACK_MARKER)]
    if ack_type in GROUP_ECHO_ACKS:
        for tag in (0xA2, 0xA3) if ack_type == 0x0A08 else (0xA2,):
            field = request.field(tag)
            if field is not None:
                fields.append((tag, field.raw[:2]))
    return charger_frame(ack_type, fields, increment=status)


def firmware_change(version: int = 2117) -> bytes:
    value = b'\x02' + version.to_bytes(2, 'little')
    return charger_frame(0x0301, [(0xA2, value)])


def charger_responder(status: int = 0,
                      realtime_frames: int = 1) -> Responder:
    """Answers reads with captured frames and writes with acks.

    A realtime trigger is answered with ``realtime_frames`` telemetry frames.
    """

    def respond(request: Frame) -> list[bytes]:
        if request.message_type == 0x0200:
            return [STATUS_SNAPSHOT]
        if request.message_type == 0x020B:
            return [REALTIME_OLD_FIRMWARE] * realtime_frames
        if request.message_type == 0x0202:
            return []
        if request.message_type == IMAGE_TIME_DISPLAY:
            # Answered with a theme status, not an ack (seen on hardware).
            enabled = request.field(0xA2)
            assert enabled is not None
            return [theme_status(bool(enabled.raw[-1]))]
        return [ack_for(request, status)]

    return respond


class FakeChannel(Channel):
    """Records sent frames; a responder answers them asynchronously."""

    _listener: ChannelListener | None
    _open: bool
    _open_count: int
    _open_failures: list[Exception]
    _sent: list[bytes]
    _responder: Responder | None

    def __init__(self, responder: Responder | None = None):
        self._listener = None
        self._open = False
        self._open_count = 0
        self._open_failures = []
        self._sent = []
        self._responder = responder

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def open_count(self) -> int:
        return self._open_count

    @property
    def sent(self) -> list[Frame]:
        return [decode_frame(data) for data in self._sent]

    def sent_types(self) -> list[int]:
        return [frame.message_type for frame in self.sent]

    def clear(self) -> None:
        self._sent.clear()

    def set_responder(self, responder: Responder | None) -> None:
        self._responder = responder

    def fail_open(self, error: Exception) -> None:
        self._open_failures.append(error)

    def deliver(self, data: bytes) -> None:
        assert self._listener is not None
        self._listener.on_message(data)

    def lose(self, error: Exception) -> None:
        self._open = False
        if self._listener is not None:
            self._listener.on_closed(error)

    def set_listener(self, listener: ChannelListener | None) -> None:
        self._listener = listener

    async def open(self) -> None:
        if self._open_failures:
            raise self._open_failures.pop(0)
        self._open = True
        self._open_count += 1

    async def close(self) -> None:
        if not self._open:
            return
        self._open = False
        if self._listener is not None:
            self._listener.on_closed(None)

    async def send(self, data: bytes) -> None:
        if not self._open:
            raise ChargerConnectionError('fake channel closed')
        self._sent.append(data)
        if self._responder is None:
            return
        loop = asyncio.get_running_loop()
        for reply in self._responder(decode_frame(data)):
            loop.call_soon(self._reply, reply)

    def _reply(self, data: bytes) -> None:
        if self._open and self._listener is not None:
            self._listener.on_message(data)
