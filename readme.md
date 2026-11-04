# anker-prime-driver

Python library to monitor and control Anker Prime chargers.

> **Status: pre-alpha, under development.** Nothing below works yet beyond the
> package skeleton.

This is an unofficial project, not affiliated with or endorsed by Anker.

## Supported chargers

| Model | Name | Connection | Notes |
|---|---|---|---|
| A2345 | Anker Prime Charger 250W | Anker cloud (account + MQTT over Wi-Fi) | needs the charger bound in the Anker app |
| A2687 | Anker Prime 160W | Bluetooth LE (local) | not tested on real hardware by this project |

Planned features: per-port voltage/current/power, port on/off, countdowns and
schedules, charging modes, display settings, energy counters, and a small
command-line tool. The API is designed so a local connection for the 250W can be
added later without changes for library users.

## Installation

Not published yet. Once released:

```bash
pip install anker-prime-driver          # cloud (250W) support
pip install anker-prime-driver[ble]     # plus Bluetooth (160W) support
```

Requires Python 3.11 or later. The library is async-only (`asyncio`).

## Development

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .[ble] -r requirements-dev.txt

ruff check
mypy
pytest
```

Formatting and import order follow the project codestyle by hand; `ruff check`
verifies line length and common lint rules. Release builds use `python -m build` and `twine`.

## Credits

The protocol knowledge comes from [Charker](https://github.com/qzz0518/Charker)
by qzz0518 (MIT), a macOS/Windows app for the same chargers; this library is an
independent Python implementation of its charger connectivity. Charker in turn
credits [SolixBLE](https://github.com/flip-dots/SolixBLE),
[Anker_Prime_160W_WebBLE](https://github.com/Hyper-Beast/Anker_Prime_160W_WebBLE),
[Anker_Prime_BLE_hacking](https://github.com/atc1441/Anker_Prime_BLE_hacking) and
[anker-solix-api](https://github.com/thomluther/anker-solix-api).

## License

MIT — see [LICENSE](LICENSE). Anker trademarks belong to their owner.
