"""A simulated A2687 and a transport connecting it to the session.

Ported from Charker's ``MockA2687Device``: the charger side of the full
negotiation with real key agreement and AES-GCM, state reports and command
replies. Additions for tests: silent or refusing stages, refused writes and
an identity check standing in for firmware bound to an account.
"""

import asyncio
import zlib
from dataclasses import dataclass, field

from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.protocol.a2687 import codes
from ankerprime.protocol.a2687.codes import (
    CONTROL_FIELDS, NEGOTIATION_GROUP, PORT_FIELDS,
    SESSION_ACTION, SESSION_GROUP, FieldId, Opcode)
from ankerprime.protocol.a2687.crypto import (
    NEGOTIATION_KEYS, CryptoError, EphemeralKey, SessionKeys)
from ankerprime.protocol.a2687.frame import (
    Frame, FrameReassembler, encode_frame)
from ankerprime.protocol.a2687.tlv import (
    Payload, ValueType, encode_fields, typed_bytes, typed_u16, typed_u8)


IDLE_CONTROL = bytes.fromhex('01002c0100002c010000')
SETTING_OPCODES = (Opcode.LANGUAGE, Opcode.SCREEN_TIMEOUT, Opcode.BRIGHTNESS,
                   Opcode.ORIENTATION, Opcode.GYROSCOPE, Opcode.FIXED_ALLOCATION)
NOTIFICATION_SIZE = 20


@dataclass
class SimulatedPort:
    on: bool
    voltage: float
    current: float
    cable: int = 0x01
    profile: int = 0x00
    countdown: int = 0

    @property
    def power(self) -> float:
        return self.voltage * self.current if self.on else 0.0


def default_ports() -> list[SimulatedPort]:
    return [SimulatedPort(True, 20.0, 3.25, 0x01, 0x01),
            SimulatedPort(True, 9.0, 2.0, 0x00, 0x02),
            SimulatedPort(False, 0.0, 0.0, 0x03)]


@dataclass
class SimulatedCharger:
    """Charger side of the protocol; ``receive`` returns the frames sent."""

    ports: list[SimulatedPort] = field(default_factory=default_ports)
    serial: str = 'ASHDMOCK00000000'
    firmware: str = 'v0.0.5.0'
    mac: bytes = bytes.fromhex('02005e100001')
    # Settings as reported; None is left out of reports.
    settings: dict[int, int] = field(default_factory=lambda: {
        0xA8: 1, 0xA9: 80, 0xAA: 0, 0xAF: 0, 0xB2: 0})
    language: int | None = None
    allocation: int | None = None
    # Identity user auth must carry; None accepts any.
    required_identity: str | None = None
    silent: set[int] = field(default_factory=set)
    refused_writes: dict[int, int] = field(default_factory=dict)
    port_refusal: int = 0
    # Answers nothing and stops reporting, as when out of range.
    muted: bool = False
    # Screen pictures: the one shown (E1, low 16 bits), stored ids and CRCs.
    screen_picture: int | None = 0x1234
    stored_pictures: dict[int, int] = field(default_factory=dict)
    cover_refusal: int = 0
    # Checkpoint index left unanswered, or answered with a wrong next index.
    cover_silent_at: int | None = None
    cover_skip_at: int | None = None
    # The picture is stored but the screen keeps the old one.
    cover_hide: bool = False
    custom_power: bytes | None = None

    requests: list[tuple[int, Payload]] = field(default_factory=list)
    identity: str | None = None
    streaming: bool = False

    _key: EphemeralKey = field(default_factory=EphemeralKey)
    _session: SessionKeys | None = None
    _open: bool = False
    _reassembler: FrameReassembler = field(default_factory=FrameReassembler)
    _transfer: dict[str, int] | None = None
    _slices: dict[int, bytes] = field(default_factory=dict)

    @property
    def session_open(self) -> bool:
        return self._open

    def reset(self) -> None:
        self._key = EphemeralKey()
        self._session = None
        self._open = False
        self.streaming = False
        self._reassembler.reset()

    def opcodes(self) -> list[int]:
        return [opcode for opcode, _ in self.requests]

    def receive(self, chunk: bytes) -> list[bytes]:
        replies = []
        for frame in self._reassembler.feed(chunk):
            replies += self._respond(frame)
        return replies

    def report(self, opcode: int = Opcode.REALTIME_REPORT) -> bytes | None:
        if self._session is None or not self._open or self.muted:
            return None
        return self._seal(SESSION_GROUP, opcode, False, self._state(),
                          self._session)

    def _respond(self, frame: Frame) -> list[bytes]:
        keys = NEGOTIATION_KEYS
        if frame.opcode >= Opcode.AES_METADATA and self._session is not None:
            keys = self._session
        try:
            request = Payload.parse(keys.open(frame.payload))
        except CryptoError:
            return []
        self.requests.append((frame.opcode, request))
        if self.muted or frame.opcode in self.silent:
            return []
        if frame.group == NEGOTIATION_GROUP:
            return self._negotiate(frame, request)
        if self._session is None or not self._open:
            return []
        return self._command(frame, request, self._session)

    def _negotiate(self, frame: Frame, request: Payload) -> list[bytes]:
        negotiation = NEGOTIATION_KEYS
        match frame.opcode:
            case codes.Opcode.INITIAL_CONNECT:
                return [self._ack(frame, [(FieldId.A1, b'\x01')], negotiation)]
            case codes.Opcode.CAPABILITY:
                return [self._ack(frame, [
                    (FieldId.A1, b'\x02'), (FieldId.A2, b'\x29\x01'),
                    (FieldId.A3, b'\x44'), (FieldId.A4, b'\x01'),
                    (FieldId.A5, b'\x02')], negotiation)]
            case codes.Opcode.BASE_INFO:
                serial = self.serial.encode()
                return [self._ack(frame, [
                    (FieldId.A1, b'\x03'), (FieldId.A2, b'Charging'),
                    (FieldId.A3, self.firmware.encode()), (FieldId.A4, serial),
                    (FieldId.A5, self.mac + serial[-11:])], negotiation)]
            case codes.Opcode.SET_CAPABILITY:
                return [self._ack(frame, [], negotiation)]
            case codes.Opcode.PUBLIC_KEY:
                point = request.get(FieldId.A1)
                if point is None:
                    return []
                self._session = SessionKeys.from_shared_secret(
                    self._key.shared_secret(point))
                return [self._ack(frame, [(FieldId.A1, self._key.public_point)],
                                  negotiation)]
            case codes.Opcode.AES_METADATA if self._session is not None:
                return [self._ack(frame, [], self._session)]
            case codes.Opcode.USER_AUTH if self._session is not None:
                identity = request.get(FieldId.A2)
                self.identity = None if identity is None else identity.decode()
                if (self.required_identity is not None
                        and self.identity != self.required_identity):
                    return [self._seal(frame.group, frame.opcode, True,
                                       b'\x01', self._session)]
                self._open = True
                return [self._ack(frame, [], self._session)]
        return []

    def _command(self, frame: Frame, request: Payload,
                 keys: SessionKeys) -> list[bytes]:
        refusal = self.refused_writes.get(frame.opcode)
        if refusal is not None:
            return [self._seal(frame.group, frame.opcode, True,
                               bytes((refusal,)), keys)]
        value = _u8(request.get(FieldId.A2))
        match frame.opcode:
            case codes.Opcode.READ_ALL:
                return [self._seal(SESSION_GROUP, Opcode.READ_ALL, True,
                                   self._state(), keys)]
            case codes.Opcode.BIND_SUCCESS:
                return [self._ack(frame, [], keys)]
            case codes.Opcode.REALTIME_TRIGGER if value is None:
                self.streaming = True
                return [self._ack(frame, [], keys)]
            case codes.Opcode.PORT_OUTPUT:
                on = _u8(request.get(FieldId.A3))
                if value is None or on is None or value >= len(self.ports):
                    return []
                if not self.port_refusal:
                    self.ports[value].on = bool(on)
                return [self._ack(frame, [
                    (FieldId.A1, typed_u8(self.port_refusal))], keys),
                    self._seal(SESSION_GROUP, Opcode.REALTIME_REPORT, False,
                               self._state(), keys)]
            case codes.Opcode.PORT_TIMER:
                duration = request.typed(FieldId.A3)
                if (value is None or duration is None or duration.block is None
                        or value >= len(self.ports)):
                    return []
                self.ports[value].countdown = int.from_bytes(duration.block,
                                                             'little')
                return [self._ack(frame, [], keys)]
            case codes.Opcode.CHARGING_MODE if value is not None:
                if value == 2 and request.get(FieldId.A3) is None:
                    return [self._seal(frame.group, frame.opcode, True,
                                       b'\x04', keys)]
                if value == 2:
                    power = request.typed(FieldId.A3)
                    self.custom_power = power.block if power else None
                self.settings[0xAA] = value
                return [self._ack(frame, [], keys)]
            case codes.Opcode.SET_SCREENSAVER | codes.Opcode.COVER_TRANSFER_START | (
                    codes.Opcode.COVER_TRANSFER_CHUNK):
                return self._cover(frame, request, keys)
        if frame.opcode in SETTING_OPCODES and value is not None:
            self._apply_setting(frame.opcode, value)
            return [self._ack(frame, [], keys)]
        return []

    def _cover(self, frame: Frame, request: Payload,
               keys: SessionKeys) -> list[bytes]:
        if frame.opcode == codes.Opcode.SET_SCREENSAVER:
            picture_id = _block_u32(request, FieldId.A4)
            crc = _block_u32(request, FieldId.A5)
            if self.stored_pictures.get(picture_id) != crc:
                # Known id, pixels missing.
                return [self._status(frame, 0x11, [], keys)]
            self.screen_picture = picture_id & 0xFFFF
            return [self._ack(frame, [], keys)]
        if frame.opcode == codes.Opcode.COVER_TRANSFER_START:
            if self.cover_refusal:
                return [self._status(frame, self.cover_refusal, [], keys)]
            size = request.typed(FieldId.A5)
            count = request.typed(codes.FieldId.A8)
            assert size is not None and count is not None
            self._transfer = {'id': _block_u32(request, FieldId.A3),
                              'crc': _block_u32(request, FieldId.A4),
                              'size': size.scalar or 0,
                              'count': count.scalar or 0}
            self._slices = {}
            return [self._ack(frame, [], keys)]
        transfer = self._transfer
        index = request.typed(FieldId.A2)
        data = request.typed(FieldId.A3)
        assert transfer is not None and index is not None and data is not None
        position = index.scalar or 0
        self._slices[position] = data.block or b''
        final = position == transfer['count'] - 1
        if not final and (position + 1) % 10:
            return []
        if position == self.cover_silent_at:
            return []
        expected = position + 1
        if position == self.cover_skip_at:
            expected = position
        status = 0
        if final:
            jpeg = b''.join(self._slices[i] for i in sorted(self._slices))
            jpeg = jpeg[:transfer['size']]
            if zlib.crc32(jpeg) == transfer['crc']:
                self.stored_pictures[transfer['id']] = transfer['crc']
                if not self.cover_hide:
                    self.screen_picture = transfer['id'] & 0xFFFF
                status = 0x10
        return [self._status(frame, status, [(FieldId.A2, typed_u16(expected))],
                             keys)]

    def _status(self, frame: Frame, status: int,
                fields: list[tuple[int, bytes]], keys: SessionKeys) -> bytes:
        return self._seal(frame.group, frame.opcode, True,
                          bytes((status,)) + encode_fields(fields), keys)

    def _apply_setting(self, opcode: int, value: int) -> None:
        match opcode:
            case codes.Opcode.LANGUAGE:
                self.language = value
            case codes.Opcode.FIXED_ALLOCATION:
                self.allocation = value
            case codes.Opcode.SCREEN_TIMEOUT:
                self.settings[0xA8] = value
            case codes.Opcode.BRIGHTNESS:
                self.settings[0xA9] = max(25, value)
            case codes.Opcode.ORIENTATION:
                self.settings[0xAF] = value
            case codes.Opcode.GYROSCOPE:
                self.settings[0xB2] = value

    def _state(self) -> bytes:
        fields: list[tuple[int, bytes]] = [
            (FieldId.A1, bytes((SESSION_ACTION,)))]
        for port, field_id, control_id in zip(self.ports, PORT_FIELDS,
                                              CONTROL_FIELDS, strict=True):
            millivolts = round(port.voltage * 1000) if port.on else 0
            milliamps = round(port.current * 1000) if port.on else 0
            centiwatts = round(port.power * 100)
            fields.append((field_id, typed_bytes(
                bytes((int(port.on),)) + millivolts.to_bytes(2, 'little')
                + milliamps.to_bytes(2, 'little')
                + centiwatts.to_bytes(2, 'little'))))
            control = IDLE_CONTROL
            if port.countdown:
                control = (b'\x01\x01' + port.countdown.to_bytes(4, 'little')
                           + port.countdown.to_bytes(4, 'little'))
            fields.append((control_id, typed_bytes(
                control + bytes((port.cable, port.profile)))))
        for tag, value in self.settings.items():
            fields.append((tag, typed_u8(value)))
        if self.screen_picture is not None:
            fields.append((codes.FieldId.COVER_STATE, typed_bytes(
                b'\x00\x03' + self.screen_picture.to_bytes(2, 'little')
                + bytes(6))))
        return b'\x00' + encode_fields(fields)

    def _ack(self, frame: Frame, fields: list[tuple[int, bytes]],
             keys: SessionKeys) -> bytes:
        return self._seal(frame.group, frame.opcode, True,
                          b'\x00' + encode_fields(fields), keys)

    def _seal(self, group: int, opcode: int, response: bool,
              plaintext: bytes, keys: SessionKeys) -> bytes:
        return encode_frame(Frame.build(group, opcode, keys.seal(plaintext),
                                        encrypted=True, response=response))


def _block_u32(request: Payload, tag: int) -> int:
    value = request.typed(tag)
    assert value is not None and value.block is not None
    return int.from_bytes(value.block, 'little')


def _u8(raw: bytes | None) -> int | None:
    if raw is None or len(raw) != 2 or raw[0] != ValueType.U8:
        return None
    return raw[1]


class SimulatedLink(Channel):
    """Bluetooth link to a ``SimulatedCharger``.

    Replies arrive asynchronously in 20-byte notifications; once the
    realtime trigger was received, reports follow every ``report_interval``.
    """

    _charger: SimulatedCharger
    _report_interval: float

    _listener: ChannelListener | None
    _open: bool
    _opens: int
    _open_failures: list[Exception]
    _sent: list[bytes]
    _reports: asyncio.Task[None] | None

    def __init__(self, charger: SimulatedCharger,
                 report_interval: float = 0.02):
        self._charger = charger
        self._report_interval = report_interval
        self._listener = None
        self._open = False
        self._opens = 0
        self._open_failures = []
        self._sent = []
        self._reports = None

    @property
    def charger(self) -> SimulatedCharger:
        return self._charger

    @property
    def opens(self) -> int:
        return self._opens

    @property
    def sent(self) -> list[bytes]:
        return self._sent

    @property
    def is_open(self) -> bool:
        return self._open

    def fail_next_open(self, error: Exception) -> None:
        self._open_failures.append(error)

    def set_listener(self, listener: ChannelListener | None) -> None:
        self._listener = listener

    async def open(self) -> None:
        if self._open_failures:
            raise self._open_failures.pop(0)
        self._opens += 1
        self._open = True
        self._charger.reset()

    async def close(self) -> None:
        if not self._open:
            return
        self._stop()
        if self._listener is not None:
            self._listener.on_closed(None)

    def drop(self) -> None:
        """The charger went away."""

        if not self._open:
            return
        self._stop()
        if self._listener is not None:
            self._listener.on_closed(ChargerConnectionError('link lost'))

    def inject(self, data: bytes) -> None:
        if self._listener is not None:
            self._listener.on_message(data)

    async def send(self, data: bytes) -> None:
        if not self._open:
            raise ChargerConnectionError('link closed')
        self._sent.append(data)
        replies = self._charger.receive(data)
        asyncio.get_running_loop().call_soon(self._deliver, replies)
        if self._charger.streaming and self._reports is None:
            self._reports = asyncio.create_task(self._stream())

    def _deliver(self, replies: list[bytes]) -> None:
        for reply in replies:
            for start in range(0, len(reply), NOTIFICATION_SIZE):
                if self._open and self._listener is not None:
                    self._listener.on_message(
                        reply[start:start + NOTIFICATION_SIZE])

    async def _stream(self) -> None:
        while self._open:
            await asyncio.sleep(self._report_interval)
            report = self._charger.report()
            if report is not None and self._open:
                self._deliver([report])

    def _stop(self) -> None:
        self._open = False
        if self._reports is not None:
            self._reports.cancel()
            self._reports = None
