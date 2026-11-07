# anker-prime-driver

Python library and command-line tool to monitor and control Anker Prime
chargers: per-port voltage, current and power, port switches, countdowns and
schedules, charging modes, display settings and energy counters.

> **Status: pre-alpha, under development.** The 250W cloud connection, energy
> counters and the command-line tool work. 160W (Bluetooth) support is
> implemented but untested on a real charger.

This is an unofficial project, not affiliated with or endorsed by Anker.

## Supported chargers

| Model | Name                     | Connection                              | Verified on hardware              |
|-------|--------------------------|-----------------------------------------|-----------------------------------|
| A2345 | Anker Prime Charger 250W | Anker cloud (account + MQTT over Wi-Fi) | everything except the UFCS switch |
| A2687 | Anker Prime 160W         | Bluetooth LE (local)                    | display settings (by Charker)     |

The 250W must be bound to an Anker account in the Anker app. Unverified
features are marked experimental: they follow the known protocol but are not
yet confirmed on that model. A local connection for the 250W may follow later
without changes for library users.

## Installation

Not published yet. Once released:

```bash
pip install anker-prime-driver          # cloud (250W) support
pip install anker-prime-driver[ble]     # plus Bluetooth (160W) support
```

Requires Python 3.11 or later.

## Quick start

```bash
ankerprime login you@example.com DE     # asks for the password
ankerprime devices
ankerprime charger add desk --serial A2345XXXXXXXXXX --default
ankerprime monitor                      # live readings until Ctrl+C
ankerprime output c1 off
```

## Documentation

- [Command-line tool](docs/cli.md): every command and option, the
  configuration folder, charger selection, exit codes.
- [Library overview](docs/library.md): usage examples, layers, the `Charger`
  interface, state model, connection behaviour, energy counters, errors.

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
verifies line length and common lint rules. Release builds use
`python -m build` and `twine`.

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
