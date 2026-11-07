"""Bluetooth LE transport of the A2687, on bleak and bleak-retry-connector.

Needs the ``ble`` extra. The charger is given as a ``BLEDevice`` (from Home
Assistant's Bluetooth stack, which may route through a proxy) or as an
address that is looked up by scanning. Notifications are passed on as they
arrive; frames spanning several notifications are joined by the session.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache, BleakNotFoundError, establish_connection)

from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.errors import ChargerConnectionError
from ankerprime.protocol.a2687.codes import (
    ADVERTISED_SERVICE, NAME_PREFIX, NOTIFY_CHARACTERISTIC,
    PRIMARY_SERVICE, WRITE_CHARACTERISTIC)
from ankerprime.utils import redact


logger = logging.getLogger(__name__)

DEFAULT_SCAN_TIMEOUT = 10.0
DEFAULT_CONNECT_ATTEMPTS = 3
# Anker's device-table entry of the A2687, found in its manufacturer data.
PRODUCT_TYPE = bytes((0xB4, 0x05))
SERVICES = (ADVERTISED_SERVICE, PRIMARY_SERVICE)


class BleTransport(Channel):
    """GATT link to one charger: writes frames, forwards notifications."""

    _device: BLEDevice | str
    _scan_timeout: float
    _connect_attempts: int

    _listener: ChannelListener | None
    _client: BleakClientWithServiceCache | None
    _write_response: bool

    def __init__(self, device: BLEDevice | str, *,
                 scan_timeout: float = DEFAULT_SCAN_TIMEOUT,
                 connect_attempts: int = DEFAULT_CONNECT_ATTEMPTS):
        self._device = device
        self._scan_timeout = scan_timeout
        self._connect_attempts = connect_attempts
        self._listener = None
        self._client = None
        self._write_response = True

    @property
    def address(self) -> str:
        if isinstance(self._device, str):
            return self._device
        return self._device.address

    @property
    def is_open(self) -> bool:
        return self._client is not None and self._client.is_connected

    def set_device(self, device: BLEDevice) -> None:
        """Use a newer ``BLEDevice`` (e.g. through another proxy) next time."""

        self._device = device

    def set_listener(self, listener: ChannelListener | None) -> None:
        self._listener = listener

    async def open(self) -> None:
        if self.is_open:
            return
        device = await self._resolve()
        try:
            client = await establish_connection(
                BleakClientWithServiceCache, device, device.name or NAME_PREFIX,
                disconnected_callback=self._on_disconnected,
                max_attempts=self._connect_attempts)
        except (BleakError, BleakNotFoundError, TimeoutError) as error:
            raise ChargerConnectionError(
                f'cannot connect to {redact(device.address)}: {error}'
            ) from error
        try:
            write = client.services.get_characteristic(WRITE_CHARACTERISTIC)
            if write is None:
                raise ChargerConnectionError('charger lacks the A2687 service')
            self._write_response = 'write' in write.properties
            await client.start_notify(NOTIFY_CHARACTERISTIC, self._on_notify)
        except (BleakError, TimeoutError) as error:
            await _disconnect(client)
            raise ChargerConnectionError(
                f'cannot subscribe to notifications: {error}') from error
        except ChargerConnectionError:
            await _disconnect(client)
            raise
        self._client = client

    async def close(self) -> None:
        client = self._client
        if client is None:
            return
        self._client = None
        await _disconnect(client)
        if self._listener is not None:
            self._listener.on_closed(None)

    async def send(self, data: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise ChargerConnectionError('Bluetooth link is closed')
        try:
            await client.write_gatt_char(WRITE_CHARACTERISTIC, data,
                                         response=self._write_response)
        except (BleakError, TimeoutError) as error:
            raise ChargerConnectionError(f'write failed: {error}') from error

    async def _resolve(self) -> BLEDevice:
        if not isinstance(self._device, str):
            return self._device
        try:
            device = await BleakScanner.find_device_by_address(
                self._device, timeout=self._scan_timeout)
        except (BleakError, OSError) as error:
            raise ChargerConnectionError(
                f'Bluetooth scan failed: {error}') from error
        if device is None:
            raise ChargerConnectionError(
                f'charger {redact(self._device)} not found')
        return device

    def _on_notify(self, _characteristic: BleakGATTCharacteristic,
                   data: bytearray) -> None:
        if self._listener is not None:
            self._listener.on_message(bytes(data))

    def _on_disconnected(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        self._client = None
        if self._listener is not None:
            self._listener.on_closed(
                ChargerConnectionError('Bluetooth link lost'))


@dataclass(init=False)
class DiscoveredCharger:
    """A charger seen by a scan.

    ``confirmed`` means model-specific evidence (name prefix or product type);
    otherwise only the FF09 service shared by the Anker family matched.
    """

    _device: BLEDevice
    _name: str | None
    _rssi: int
    _confirmed: bool

    def __init__(self, device: BLEDevice, name: str | None, rssi: int,
                 confirmed: bool):
        self._device = device
        self._name = name
        self._rssi = rssi
        self._confirmed = confirmed

    @property
    def device(self) -> BLEDevice:
        return self._device

    @property
    def address(self) -> str:
        return self._device.address

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def rssi(self) -> int:
        return self._rssi

    @property
    def confirmed(self) -> bool:
        return self._confirmed


def advertisement_match(name: str | None, service_uuids: Sequence[str],
                        service_data: Mapping[str, bytes],
                        manufacturer_data: Mapping[int, bytes]
                        ) -> bool | None:
    """Classify an advertisement.

    True for an A2687, False for a possible one (only the service shared by
    the Anker family matched), None for anything else.
    """

    if name and name.upper().startswith(NAME_PREFIX):
        return True
    for company, data in manufacturer_data.items():
        if PRODUCT_TYPE in company.to_bytes(2, 'little') + data:
            return True
    uuids = {uuid.lower() for uuid in (*service_uuids, *service_data)}
    if uuids & set(SERVICES):
        return False
    return None


async def discover_chargers(timeout: float = DEFAULT_SCAN_TIMEOUT
                            ) -> list[DiscoveredCharger]:
    """Scan for A2687 chargers, model-confirmed ones first, then by signal."""

    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except (BleakError, OSError) as error:
        raise ChargerConnectionError(
            f'Bluetooth scan failed: {error}') from error
    chargers = []
    for device, advertisement in found.values():
        confirmed = _match(device, advertisement)
        if confirmed is not None:
            chargers.append(DiscoveredCharger(
                device, advertisement.local_name or device.name,
                advertisement.rssi, confirmed))
    chargers.sort(key=lambda charger: (not charger.confirmed, -charger.rssi))
    return chargers


def _match(device: BLEDevice, advertisement: AdvertisementData) -> bool | None:
    return advertisement_match(advertisement.local_name or device.name,
                               advertisement.service_uuids,
                               advertisement.service_data,
                               advertisement.manufacturer_data)


async def _disconnect(client: BleakClientWithServiceCache) -> None:
    try:
        await client.disconnect()
    except (BleakError, TimeoutError) as error:
        logger.debug('Bluetooth disconnect failed: %s', error)
