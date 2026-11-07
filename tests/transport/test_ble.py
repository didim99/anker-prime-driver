"""Bluetooth transport tests with bleak replaced by fakes."""

from collections.abc import Callable
from typing import Any

import pytest
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from ankerprime.interfaces.channel import ChannelListener
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.protocol.a2687.codes import (
    ADVERTISED_SERVICE, NOTIFY_CHARACTERISTIC, WRITE_CHARACTERISTIC)
from ankerprime.transport import ble
from ankerprime.transport.ble import (
    BleTransport, advertisement_match, discover_chargers)


ADDRESS = 'AA:BB:CC:DD:EE:FF'
DEVICE = BLEDevice(ADDRESS, 'ASHDJW1234', None)

Notify = Callable[[Any, bytearray], None]


class FakeCharacteristic:
    def __init__(self, properties: list[str]):
        self.properties = properties


class FakeServices:
    def __init__(self, write: FakeCharacteristic | None):
        self._write = write

    def get_characteristic(self, uuid: str) -> FakeCharacteristic | None:
        return self._write if uuid == WRITE_CHARACTERISTIC else None


class FakeClient:
    def __init__(self, disconnected: Callable[['FakeClient'], None],
                 write: FakeCharacteristic | None):
        self.disconnected = disconnected
        self.services = FakeServices(write)
        self.is_connected = True
        self.notify: Notify | None = None
        self.writes: list[tuple[bytes, bool | None]] = []
        self.fail_writes = False

    async def start_notify(self, uuid: str, callback: Notify) -> None:
        assert uuid == NOTIFY_CHARACTERISTIC
        self.notify = callback

    async def write_gatt_char(self, uuid: str, data: bytes,
                              response: bool | None = None) -> None:
        assert uuid == WRITE_CHARACTERISTIC
        if self.fail_writes:
            raise BleakError('write failed')
        self.writes.append((bytes(data), response))

    async def disconnect(self) -> None:
        self.is_connected = False

    def lose(self) -> None:
        self.is_connected = False
        self.disconnected(self)


class Recorder(ChannelListener):
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.closed: list[Exception | None] = []

    def on_message(self, data: bytes) -> None:
        self.messages.append(data)

    def on_closed(self, error: Exception | None) -> None:
        self.closed.append(error)


class Bluetooth:
    """Replaces the bleak entry points used by the transport."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.clients: list[FakeClient] = []
        self.connect_error: Exception | None = None
        self.write = FakeCharacteristic(['write-without-response', 'write'])
        self.found: BLEDevice | None = DEVICE
        self.advertisements: dict[str, tuple[BLEDevice,
                                             AdvertisementData]] = {}
        monkeypatch.setattr(ble, 'establish_connection', self.connect)
        monkeypatch.setattr(BleakScanner, 'find_device_by_address',
                            self.find)
        monkeypatch.setattr(BleakScanner, 'discover', self.discover)

    async def connect(self, client_class: type, device: BLEDevice, name: str,
                      disconnected_callback: Callable[[FakeClient], None],
                      max_attempts: int) -> FakeClient:
        if self.connect_error is not None:
            raise self.connect_error
        client = FakeClient(disconnected_callback, self.write)
        self.clients.append(client)
        return client

    async def find(self, address: str, timeout: float) -> BLEDevice | None:
        return self.found

    async def discover(self, timeout: float, return_adv: bool
                       ) -> dict[str, tuple[BLEDevice, AdvertisementData]]:
        return self.advertisements


@pytest.fixture
def bluetooth(monkeypatch: pytest.MonkeyPatch) -> Bluetooth:
    return Bluetooth(monkeypatch)


def advertisement(name: str | None, rssi: int,
                  services: tuple[str, ...] = ()) -> AdvertisementData:
    return AdvertisementData(local_name=name, manufacturer_data={},
                             service_data={}, service_uuids=list(services),
                             tx_power=None, rssi=rssi, platform_data=())


async def test_open_send_receive_close(bluetooth: Bluetooth) -> None:
    transport = BleTransport(DEVICE)
    recorder = Recorder()
    transport.set_listener(recorder)
    await transport.open()
    assert transport.is_open and transport.address == ADDRESS
    await transport.open()
    assert len(bluetooth.clients) == 1
    client = bluetooth.clients[0]
    await transport.send(b'\xff\x09')
    assert client.writes == [(b'\xff\x09', True)]
    assert client.notify is not None
    client.notify(None, bytearray(b'\x01\x02'))
    assert recorder.messages == [b'\x01\x02']
    await transport.close()
    assert not client.is_connected
    assert recorder.closed == [None]
    await transport.close()
    assert recorder.closed == [None]
    with pytest.raises(ChargerConnectionError):
        await transport.send(b'\x00')


async def test_write_without_response_when_only_that_is_offered(
        bluetooth: Bluetooth) -> None:
    bluetooth.write = FakeCharacteristic(['write-without-response'])
    transport = BleTransport(DEVICE)
    await transport.open()
    await transport.send(b'\x01')
    assert bluetooth.clients[0].writes == [(b'\x01', False)]


async def test_lost_link_is_reported(bluetooth: Bluetooth) -> None:
    transport = BleTransport(DEVICE)
    recorder = Recorder()
    transport.set_listener(recorder)
    await transport.open()
    bluetooth.clients[0].lose()
    assert not transport.is_open
    assert isinstance(recorder.closed[0], ChargerConnectionError)


async def test_failures_become_connection_errors(bluetooth: Bluetooth
                                                 ) -> None:
    transport = BleTransport(DEVICE)
    bluetooth.connect_error = BleakError('no slot')
    with pytest.raises(ChargerConnectionError):
        await transport.open()
    bluetooth.connect_error = None
    bluetooth.write = None  # type: ignore[assignment]
    with pytest.raises(ChargerConnectionError):
        await transport.open()
    assert not bluetooth.clients[-1].is_connected
    bluetooth.write = FakeCharacteristic(['write'])
    await transport.open()
    bluetooth.clients[-1].fail_writes = True
    with pytest.raises(ChargerConnectionError):
        await transport.send(b'\x01')


async def test_address_is_looked_up(bluetooth: Bluetooth) -> None:
    transport = BleTransport(ADDRESS)
    await transport.open()
    assert transport.is_open
    bluetooth.found = None
    missing = BleTransport('11:22:33:44:55:66')
    with pytest.raises(ChargerConnectionError):
        await missing.open()
    newer = BLEDevice('11:22:33:44:55:66', None, None)
    missing.set_device(newer)
    await missing.open()
    assert missing.is_open


def test_advertisement_match() -> None:
    assert advertisement_match('ASHDJW1A2B', [], {}, {}) is True
    assert advertisement_match(None, [], {}, {0x05B4: b'\xb4\x05'}) is True
    assert advertisement_match('Solix', [ADVERTISED_SERVICE], {}, {}) is False
    assert advertisement_match('Speaker', [], {}, {0x004C: b'\x01'}) is None


async def test_discover_sorts_confirmed_first(bluetooth: Bluetooth) -> None:
    family = BLEDevice('00:00:00:00:00:01', None, None)
    other = BLEDevice('00:00:00:00:00:02', 'Speaker', None)
    bluetooth.advertisements = {
        'a': (family, advertisement('Solix', -40, (ADVERTISED_SERVICE,))),
        'b': (DEVICE, advertisement(None, -80)),
        'c': (other, advertisement('Speaker', -30)),
    }
    found = await discover_chargers(1.0)
    assert [(charger.address, charger.confirmed, charger.rssi)
            for charger in found] == [(ADDRESS, True, -80),
                                      ('00:00:00:00:00:01', False, -40)]
    assert found[0].name == 'ASHDJW1234'
    assert found[0].device is DEVICE
