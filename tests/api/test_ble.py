"""Bluetooth charger factory tests."""

from ankerprime.api.ble import ble_charger
from ankerprime.device.a2687 import A2687Device
from ankerprime.model.state import DeviceInfo
from tests.device.a2687_charger import SimulatedCharger, SimulatedLink
from tests.utils import MemoryStore


async def test_factory_builds_a_disconnected_charger() -> None:
    charger = ble_charger('AA:BB:CC:DD:EE:FF', owner_id='owner',
                          keep_connection=False)
    assert isinstance(charger, A2687Device)
    assert not charger.keep_connection
    assert charger.product.code == 'A2687'
    assert not charger.state.available


async def test_energy_needs_the_serial() -> None:
    store = MemoryStore()
    unknown = ble_charger('AA:BB:CC:DD:EE:FF', energy_store=store)
    await unknown.reset_energy()
    assert store.documents == {}
    known = ble_charger('AA:BB:CC:DD:EE:FF', energy_store=store,
                        info=DeviceInfo(serial='ASHDSERIAL01'))
    await known.reset_energy()
    assert list(store.documents) == ['energy-ashdserial01']


async def test_transport_can_be_replaced() -> None:
    charger = SimulatedCharger()
    device = ble_charger('AA:BB:CC:DD:EE:FF',
                         transport=SimulatedLink(charger))
    try:
        await device.connect()
        assert device.state.info.serial == charger.serial
    finally:
        await device.disconnect()
