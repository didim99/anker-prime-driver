"""Bluetooth-connected chargers (Anker Prime 160W, A2687); needs ``[ble]``."""

from bleak.backends.device import BLEDevice

from ankerprime.device.a2687 import DEFAULT_COUNTRY, A2687Device, A2687Timings
from ankerprime.interfaces.channel import Channel
from ankerprime.interfaces.charger import Charger
from ankerprime.interfaces.storage import DocumentStore
from ankerprime.model.state import DeviceInfo
from ankerprime.session.a2687 import A2687Session
from ankerprime.transport.ble import (
    BleTransport, DiscoveredCharger, discover_chargers)
from ankerprime.utils import Clock


__all__ = ['DiscoveredCharger', 'ble_charger', 'discover_chargers']


def ble_charger(device: BLEDevice | str, *, owner_id: str | None = None,
                client_id: str | None = None,
                country: str = DEFAULT_COUNTRY,
                keep_connection: bool = True,
                info: DeviceInfo | None = None,
                energy_store: DocumentStore | None = None,
                timings: A2687Timings | None = None,
                clock: Clock | None = None,
                transport: Channel | None = None) -> Charger:
    """Return a charger reached over Bluetooth LE.

    ``device`` is a ``BLEDevice`` or an address to scan for. ``owner_id`` is
    the Anker account ID the charger is bound to; firmware that checks it
    ignores other identities. ``client_id`` is the fallback identity (random
    when omitted; keep it stable across runs). ``country`` is the account's
    two-letter country code. ``info`` gives the known identity. Energy
    counters are kept in ``energy_store`` under the charger's serial.
    ``transport`` replaces the Bluetooth link (tests, custom links).
    """

    link = transport if transport is not None else BleTransport(device)
    session = A2687Session(link, client_id=client_id, owner_id=owner_id,
                           clock=clock)
    return A2687Device(session, info, keep_connection=keep_connection,
                       country=country, owner_id=owner_id,
                       energy_store=energy_store, timings=timings,
                       clock=clock)
