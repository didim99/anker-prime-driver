# Library overview

`ankerprime` is an async-only (`asyncio`) library. Embedders use one
high-level interface, `Charger`, whatever the model and connection; everything
below it is internal and may change between versions.

- [Usage](#usage)
- [Layers](#layers)
- [The Charger interface](#the-charger-interface)
- [State model](#state-model)
- [Connections](#connections)
- [Energy counters](#energy-counters)
- [Storage](#storage)
- [Errors](#errors)
- [Logging](#logging)

## Usage

### 250W through the Anker cloud

```python
import asyncio

import aiohttp

from ankerprime.api import CloudAccount
from ankerprime.cloud.auth import AccountCredentials
from ankerprime.cloud.http import AiohttpClient
from ankerprime.model import ChargerState, OutputId


async def main() -> None:
    ready = asyncio.Event()

    def on_state(state: ChargerState) -> None:
        if state.available and state.outputs:
            ready.set()
            print(f'{state.total_power:.1f} W', {
                port.value: f'{reading.power:.1f} W'
                for port, reading in state.ports.items()})

    async with aiohttp.ClientSession() as session:
        account = await CloudAccount.login(
            AiohttpClient(session),
            AccountCredentials('you@example.com', 'password', 'DE'))
        try:
            charger = (await account.chargers())[0]
            charger.subscribe(on_state)
            await charger.connect()
            # Writes need the charger's full state first.
            await asyncio.wait_for(ready.wait(), 30)
            await charger.set_output(OutputId.C1, False)
            await asyncio.sleep(10)
            await charger.set_output(OutputId.C1, True)
        finally:
            await account.close()


asyncio.run(main())
```

To avoid signing in on every start, keep the token: `account.authentication`
and every renewed token passed to `on_authentication=` serialise with
`to_document()`; restore with `Authentication.from_document(...)` and create
the account directly with
`CloudAccount(http, authentication, credentials=...)`. With `credentials`, an
expired token is renewed by signing in again; without them an
`AuthenticationError` reaches you.

### 160W over Bluetooth

Needs the `ble` extra.

```python
import asyncio
from pathlib import Path

from ankerprime.api.ble import ble_charger, discover_chargers
from ankerprime.model import ScreenOrientation
from ankerprime.storage import JsonFileStore


async def main() -> None:
    found = [charger for charger in await discover_chargers()
             if charger.confirmed]
    charger = ble_charger(found[0].device, owner_id='<Anker user ID>',
                          client_id='<stable random hex>', country='DE',
                          energy_store=JsonFileStore(Path('anker-data')))
    await charger.connect()
    try:
        await charger.refresh()
        print(charger.state.info.serial, charger.state.total_power)
        await charger.set_screen_orientation(ScreenOrientation.LEFT)
    finally:
        await charger.disconnect()


asyncio.run(main())
```

`device` may also be an address string, which is then scanned for; Home
Assistant passes its own `BLEDevice` (which may route through a proxy) and
`ble_device_callback`, which supplies the newest one before each connection
attempt. `new_client_id()` makes a fallback identity to store once.

## Layers

```
api        Charger interface, CloudAccount, ble_charger      ← used by embedders
device     A2345Device, A2687Device                          state, writes, timers, reconnects
protocol   a2345 and a2687 codecs, shared FF09 envelope      pure, synchronous, no I/O
session    A2687Session (ECDH + AES-GCM)                     encryption and user auth
transport  cloud MQTT channel, BleTransport                  moves bytes only
cloud      Anker account login, regions, bound devices, MQTT credentials
energy     energy meter, recorder, store
model      products, capabilities, state, enums, errors      shared by every layer
storage    DocumentStore implementations
```

Dependencies point downwards. Layers meet at the abstract interfaces in
`ankerprime.interfaces`:

- `Channel` / `ChannelListener`: a bidirectional message channel. Transport
  is a channel of raw bytes; a session wraps one and is itself a channel of
  plaintext frames, so device logic never sees encryption or transport
  framing. Devices reconnect by reopening their channel, so cloud and
  Bluetooth share the same device logic.
- `Charger`: the public charger API.
- `DocumentStore`: async key → JSON document storage.
- `HttpClient`, `MqttConnector`: network adapters (aiohttp and aiomqtt
  implementations are included; tests and embedders can substitute their
  own).

## The Charger interface

| Member | Meaning |
|----------------------------|---------------------------------------------------------------------------------|
| `product` | model definition: ports, outputs, capabilities, value ranges |
| `state` | latest `ChargerState`, available before the first connection |
| `subscribe(listener)` | call `listener(state)` with every new state; returns an unsubscribe function |
| `connect()` | connect; raises when the first attempt fails, then reconnects in the background |
| `disconnect()` | stop and save energy counters |
| `refresh()` | read the full state again |
| `last_error` | the error that stopped reconnecting (None while trying) |
| `supports(capability)` | the unit has the capability and it is implemented |
| `reset_energy(ports=None)` | zero energy counters, also while disconnected |
| one method per capability | `set_output`, `start_countdown`, `set_charging_mode`, `set_brightness`, … |

Listeners run on the event loop and must not block; an exception in one is
logged and does not affect others. States are immutable and published only
when they change.

Every command checks its capability first (`UnsupportedCapabilityError`) and
its arguments against the product (`InvalidParameterError`). A command
returns once the charger acknowledged it; the following state reflects the
change.

### Capabilities

`product.capabilities` maps each `Capability` to a `Verification`:

- `verified`: confirmed on a real charger;
- `experimental`: follows the protocol, not yet confirmed on that model;
- `planned`: known but not implemented; `supports()` returns False.

`state.capabilities` narrows the product's set by what the unit's firmware
reports (for example UFCS on older 250W firmware).

## State model

`ChargerState` (`ankerprime.model`):

- `info`: serial, name, firmware and hardware version, MAC address;
- `available`: readings are live; `updated_at`: last report;
- `ports`: `PortReading` per `PortId` — status, voltage, current, power,
  `delivering`, cable rating, charging protocol, phone brand, USB IDs;
- `outputs`: `OutputState` per `OutputId` — on/off, `Countdown` (with
  `ends_at`), power-on/off `Schedule`, preferred, fault. A port is a physical
  connector; an output is what one switch controls (the 250W's two USB-A
  ports share one output);
- `settings`: charging mode, fixed allocation, display (brightness, timeout,
  orientation, rotation, language, knob, hour format), clock screensaver,
  switches;
- `over_temperature`, `error_code`;
- `energy`, `total_energy` (Wh), `total_power` (W).

Values a model or firmware does not report are None. String enum values are
stable identifiers; `ankerprime.model.serialize.state_to_dict` turns a state
into a JSON-ready dict.

## Connections

### Cloud (250W)

`CloudAccount` signs in (or uses a stored token), lists bound devices and
returns one `Charger` per cloud-reachable serial. All chargers of an account
share one MQTT connection, opened with the first `connect` and closed with the
last `disconnect`. `CloudAccount.close()` disconnects everything.

Behavior, following the official app:

- realtime telemetry renewed every 8 s; readings stale after 15 s without
  telemetry; a silent link is recycled after 45 s;
- lost links reconnect with growing delays (1 s … 60 s) until an
  `AuthenticationError` stops them (then `last_error` is set);
- settings are read with a snapshot on connect, after writes, after changes
  made by other clients, and every 30 s, since the cloud does not push them;
- writes wait for the acknowledgement, resend once and then raise
  `ChargerTimeoutError`; a refused write raises `WriteRejectedError`.

Timings are adjustable with `A2345Timings`.

### Bluetooth (160W)

`ble_charger(device, owner_id=, client_id=, country=, keep_connection=,
info=, energy_store=, timings=)` returns a `Charger`; `discover_chargers()`
scans. Each connection negotiates a session key (P-256 + AES-GCM) and
authenticates with the owner ID (the Anker account the charger is bound to)
or, as a fallback older firmware accepts, `client_id`. Keep `client_id`
stable across runs. A charger that refuses the identity and sends no data
stops reconnecting with `AccountRefusedError`.

Two modes:

- `keep_connection=True` (default): polls every 6 s; readings stale after
  20 s; reconnects with delays of 2 s … 30 s;
- `keep_connection=False`: connects every `update_interval` (60 s) and for
  each command, then disconnects after a few seconds. A charger accepts one
  connection at a time, so this leaves room for the official app.

Timings are adjustable with `A2687Timings`. The 160W support has not been
run against a real charger by this project.

## Energy counters

Chargers do not report energy. Each device integrates power per port from
every report (trapezoidal rule; gaps over two minutes count nothing) into
counters that only grow, published as `state.energy`.

With an `energy_store` (`CloudAccount(energy_store=...)`,
`ble_charger(energy_store=...)`) the counters persist under the charger's
serial: loaded on the first connect, saved every minute and on
`disconnect`. Several processes may count the same charger into one store;
saving merges, and a reset made elsewhere (`EnergyStore.reset`) is followed
on the next save.

## Storage

The library owns its document formats; embedders provide only a
`DocumentStore` (load, save, delete JSON documents by key). `JsonFileStore`
keeps one owner-only JSON file per key in a folder; Home Assistant uses its
own `Store`. Documents: `Authentication` and `AccountCredentials`
(`to_document` / `from_document`), and energy records (`energy-<serial>`).

## Errors

All library errors derive from `AnkerPrimeError` (`ankerprime.model`):

| Error | Meaning |
|------------------------------|-----------------------------------------------------------------|
| `AuthenticationError` | credentials or token rejected; user action needed |
| `AccountRefusedError` | (subclass) a 160W refused the user identity; set the owner ID |
| `ChargerConnectionError` | charger or cloud unreachable, link lost |
| `ChargerTimeoutError` | no answer in time (also a `TimeoutError`) |
| `ProtocolError` | unexpected data from the charger or the cloud |
| `InvalidParameterError` | argument outside what the charger accepts (also a `ValueError`) |
| `UnsupportedCapabilityError` | the charger or this library lacks the feature |
| `WriteRejectedError` | the charger refused a write (`status`) |
| `UnknownProductError` | not a supported model |

## Logging

Loggers live under `ankerprime`. Serials, account IDs, tokens and MQTT topics
are shortened to their last four characters; keys and passwords are never
logged.
