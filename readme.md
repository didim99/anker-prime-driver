# anker-prime-driver

Python library to monitor and control Anker Prime chargers.

> **Status: pre-alpha, under development.** The 250W cloud connection, energy
> counters and the command-line tool work; the 160W is not done yet.

This is an unofficial project, not affiliated with or endorsed by Anker.

## Supported chargers

| Model | Name                     | Connection                              | Notes                                       |
|-------|--------------------------|-----------------------------------------|---------------------------------------------|
| A2345 | Anker Prime Charger 250W | Anker cloud (account + MQTT over Wi-Fi) | needs the charger bound in the Anker app    |
| A2687 | Anker Prime 160W         | Bluetooth LE (local)                    | not tested on real hardware by this project |

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

## Command-line tool

The package installs an `ankerprime` command (also `python -m ankerprime`).
Sign in once; the login is stored in `~/.config/ankerprime` (or
`%APPDATA%\ankerprime` on Windows), readable by your user only.

```bash
ankerprime regions                     # country codes Anker accepts
ankerprime login you@example.com DE    # asks for the password
ankerprime logout
ankerprime devices                     # chargers bound to the account
ankerprime status                      # full state once (--json for JSON)
ankerprime monitor                     # live port readings until Ctrl+C
ankerprime monitor --detail            # same, with outputs and settings
```

Changing settings:

```bash
ankerprime output c1 off
ankerprime countdown c2 1h30m          # or "cancel"
ankerprime schedule c3 on 07:30 --days weekdays
ankerprime mode port-priority
ankerprime display --brightness 60 --screen-timeout 5min
ankerprime clock on --theme style_2
```

Energy per port is not reported by the charger; the library integrates it
from the live power readings (nothing is counted while no readings arrive).
`monitor` keeps these counters in the same folder (`energy-<serial>.json`),
so they continue across runs; other commands only show them.
`ankerprime energy-reset [port ...]` sets them to zero (all ports by default;
with `--serial` it works offline). A `monitor` running at the time follows the
reset within a minute.

With several chargers on the account, pick one with `--serial`. Run
`ankerprime --help` or `ankerprime <command> --help` for every option.
Commands marked experimental print a note: they follow the protocol but are
not confirmed on a real charger yet.

A second Anker account for this tool is recommended, so the login used by the
Anker app stays untouched.

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
