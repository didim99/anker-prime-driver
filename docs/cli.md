# Command-line tool

The package installs an `ankerprime` command (also `python -m ankerprime`). It
is built on the public library API only, so it doubles as a usage example and
as a test tool for real chargers.

- [Global options](#global-options)
- [Configuration folder](#configuration-folder)
- [Choosing a charger](#choosing-a-charger)
- [Account commands](#account-commands): `login`, `logout`, `regions`, `devices`
- [Charger management](#charger-management): `scan`, `charger`
- [Reading](#reading): `status`, `monitor`
- [Control](#control): `output`, `countdown`, `schedule`, `mode`, `priority`,
  `allocation`, `display`, `clock`, `clock-schedule`, `holiday`, `switch`
- [Clock pictures](#clock-pictures-250w): `clock-picture`
- [Custom charging mode](#custom-charging-mode-250w): `labs`, `custom-mode`
- [Energy counters](#energy-counters): `energy-reset`
- [Exit codes](#exit-codes)

## Global options

Given before the command name:

| Option                        | Meaning                                                        |
|-------------------------------|----------------------------------------------------------------|
| `--config-dir DIR`            | configuration folder (see below)                               |
| `--color auto\|always\|never` | colored output; `auto` colors terminals only                   |
| `--debug`                     | log library details; identifiers are shortened to 4 characters |
| `--version`                   | print the version                                              |

With `--color auto`, the `NO_COLOR` environment variable turns colors off and
`FORCE_COLOR` turns them on. `TERM=dumb` counts as no terminal. JSON output is
never colored. On Windows, colors and the in-place `monitor` view need a
console with virtual-terminal support (Windows 10 and later); otherwise the
output is plain.

Every command has `--help`.

## Configuration folder

`$ANKERPRIME_CONFIG_DIR` when set, otherwise `%APPDATA%\ankerprime` on
Windows and `$XDG_CONFIG_HOME/ankerprime` (default `~/.config/ankerprime`)
elsewhere. Every file in it is readable by your user only.

| File                   | Contents                                                    |
|------------------------|-------------------------------------------------------------|
| `authentication.json`  | Anker token of the signed-in account                        |
| `credentials.json`     | email, password and country for signing in again (optional) |
| `chargers.json`        | saved charger names and the default charger                 |
| `bluetooth.json`       | the tool's random Bluetooth identity                        |
| `energy-<serial>.json` | energy counters of one charger                              |

## Choosing a charger

Every command that works on a charger (reading, control) accepts:

| Option                 | Meaning                                               |
|------------------------|-------------------------------------------------------|
| `-c`, `--charger NAME` | a charger saved with `charger add`                    |
| `--serial SERIAL`      | a cloud charger (250W) of the signed-in account       |
| `--ble ADDRESS`        | a 160W over Bluetooth (addresses come from `scan`)    |
| `--owner-id ID`        | 160W only: Anker account ID the charger is bound to   |
| `--country CC`         | 160W only: account country code sent to the charger   |
| `--timeout SECONDS`    | how long to wait for the charger's state (default 30) |

`-c`, `--serial` and `--ble` exclude each other. Without any of them the
default charger is used (`charger default`); without a default, the account's
only cloud charger. With several cloud chargers and no default, the command
lists their serials and asks for one.

The command connects, waits until the charger has reported its full state,
acts, and disconnects.

**160W identity.** Newer 160W firmware only talks to the Anker account the
charger is bound to. The owner ID is taken from, in order: `--owner-id`, the
saved charger, the signed-in account (`login` prints its user ID). Without a
match such a charger refuses the session and the command exits with code 3.
The country comes from `--country`, the saved charger, the signed-in account,
else `US`. The tool's own random identity in `bluetooth.json` is the fallback
older firmware accepts.

A Bluetooth charger accepts one connection at a time: close the Anker app on
your phone first, and run one command at a time.

## Account commands

The 250W is reached through the Anker cloud, so it needs a signed-in Anker
account to which the charger is bound in the Anker app. A second account for
this tool, with the charger shared to it, keeps the app's login untouched.

### `login EMAIL COUNTRY`

Signs in and stores the login. Asks for the password; `--password-stdin`
reads it from standard input instead. The country decides Anker's server
(`regions` lists the codes; mainland China needs an SMS login, which is not
supported).

`--no-save-password` stores only the token: when it expires, sign in again.
Otherwise the credentials are kept and an expired token is renewed
automatically.

```bash
ankerprime login you@example.com DE
echo "$PASSWORD" | ankerprime login you@example.com DE --password-stdin
```

### `logout`

Removes the stored token and credentials.

### `regions`

Lists the country codes Anker accepts with the server each one uses.

### `devices`

Lists the devices bound to the account: serial, model, name, Wi-Fi state and
firmware.

## Charger management

### `scan [--timeout SECONDS]`

Scans for 160W chargers over Bluetooth (default 10 s) and lists address,
name, signal strength and match: `A2687` for model-specific evidence,
`possible` for devices that only advertise the service the whole Anker family
shares (power stations, other chargers). Needs the `ble` extra.

### `charger`

Saved chargers, so commands need no serial or address.

| Action                                                          | Meaning                                    |
|-----------------------------------------------------------------|--------------------------------------------|
| `charger` or `charger list`                                     | list saved chargers; `*` marks the default |
| `charger add NAME --serial SERIAL`                              | save a cloud charger                       |
| `charger add NAME --ble ADDRESS [--owner-id ID] [--country CC]` | save a 160W                                |
| `charger add ... --default`                                     | also make it the default                   |
| `charger remove NAME`                                           | forget a charger                           |
| `charger default`                                               | show the default charger                   |
| `charger default NAME`                                          | choose the default charger                 |
| `charger default --clear`                                       | use no default                             |

Names are case-insensitive: up to 32 letters, digits, `_`, `.` or `-`. Adding
an existing name replaces it. A saved 160W records its serial on the first
connection, which `energy-reset` needs.

```bash
ankerprime charger add desk --serial A2345XXXXXXXXXX --default
ankerprime charger add lamp --ble AA:BB:CC:DD:EE:FF --country DE
ankerprime status -c lamp
```

## Reading

### `status [--json]`

Prints the full state once:

- header: name, model, firmware, live state, last update and its age, total
  power and energy;
- ports: output switch, status (charging, active, idle), voltage, current,
  power, energy and details (cable rating, charging protocol, phone brand,
  USB vendor:product ID);
- outputs: running countdowns, schedules, preferred ports, faults;
- settings: charging mode, display, clock screensaver, switches.

`--json` prints the same state as one JSON object.

### `monitor [--detail] [--json] [--interval SECONDS] [--count N]`

Live readings until Ctrl+C. On a terminal the view is redrawn in place every
`--interval` seconds (default 1): header and port table, plus everything
`status` shows with `--detail`. The age of the last update keeps counting, so
a silent charger is visible, and a `Running since` line shows how long the
monitor has run.

Piped or with `--json`, one state is printed per update (JSON: one object per
line), at most one per interval. `--count N` stops after N views (default 0:
never).

`monitor` is the only command that saves energy counters.

## Control

Every control command reports what it changed. Features not yet confirmed on
a real charger of that model print a "not yet verified" note after success
(see the model table in the [readme](../readme.md#supported-chargers)). A
feature the charger lacks fails with "capability not supported".

Outputs are `c1`…`c4` and `usb_a` (also `a` or `usb-a`; the 250W's two USB-A
ports share one switch). The 160W has `c1`…`c3`.

| Command          | Arguments                                                | Models | Meaning                                                                                          |
|------------------|----------------------------------------------------------|--------|--------------------------------------------------------------------------------------------------|
| `output`         | `OUTPUT on\|off`                                         | both   | switch an output                                                                                 |
| `countdown`      | `OUTPUT DURATION\|cancel`                                | both   | switch the output off after `90`, `15m`, `1h30m` … (up to 24 h), or cancel                       |
| `schedule`       | `OUTPUT on\|off HH:MM [--days DAYS] [--disable]`         | 250W   | daily power-on or power-off time; `--disable` stores the slot switched off                       |
| `mode`           | `MODE`                                                   | both   | charging mode; 250W: `ai`, `port_priority`, `dual_laptop`, `low_current`; 160W: `ai`, `standard` |
| `priority`       | `[OUTPUT ...]`                                           | 250W   | up to two preferred USB-C outputs of the port-priority mode; none clears                         |
| `allocation`     | `c1_priority\|dual_laptop`                               | 160W   | fixed allocation of the standard mode                                                            |
| `display`        | options below                                            | both   | display settings                                                                                 |
| `clock`          | `on\|off [--theme THEME]`                                | 250W   | clock screensaver; themes `style_1`…`style_3`                                                    |
| `clock-schedule` | `HH:MM HH:MM [--days DAYS]`                              | 250W   | when the clock screensaver is shown                                                              |
| `holiday`        | `on\|off`                                                | 250W   | holiday themes of the clock                                                                      |
| `switch`         | `max-compatibility\|device-identification\|ufcs on\|off` | 250W   | charger switches                                                                                 |

Enum values accept `-` for `_`, in any case (`port-priority`). `DAYS` is a
comma-separated list of `mon` … `sun`, or `daily`, `weekdays`, `weekend`,
`once`.

`display` applies every option given:

| Option                 | Models | Values                                                                                       |
|------------------------|--------|----------------------------------------------------------------------------------------------|
| `--brightness PERCENT` | both   | 250W 20–100 in steps of 5; 160W 25–100                                                       |
| `--screen-timeout`     | both   | 250W `always_on`, `30s`, `1min`, `5min`, `30min`; 160W `30s`, `1min`, `5min`, `30min`, `12h` |
| `--knob`               | 250W   | `forward`, `backward`                                                                        |
| `--hours`              | 250W   | `12h`, `24h`                                                                                 |
| `--orientation`        | 160W   | `up`, `left`, `down`, `right`                                                                |
| `--auto-rotation`      | 160W   | `on`, `off`                                                                                  |
| `--language`           | 160W   | `en`, `zh_hans`, `ja`, `de`                                                                  |

```bash
ankerprime output c1 off
ankerprime countdown c2 1h30m
ankerprime schedule c3 on 07:30 --days weekdays
ankerprime mode port-priority
ankerprime priority c1 c2
ankerprime display --brightness 60 --screen-timeout 5min
ankerprime clock on --theme style_2
ankerprime clock-schedule 22:00 07:00 --days weekend
ankerprime switch ufcs on
```

On the 250W, `switch max-compatibility` and `switch device-identification`
exclude each other: switching one on first switches the other off. Both are
also stored in the Anker account, as the official app does.

## Clock pictures (250W)

Besides its three built-in styles, the clock screensaver can show a picture:
one of the Anker cloud catalogue, or one of up to eight own pictures kept in
the Anker account (shared with the Anker app). The charger downloads a picture
itself over Wi-Fi; applying one waits up to 45 s for its download report and
switches the clock on. `clock on|off` keeps the picture shown.

### `clock-picture [ACTION]`

| Action           | Arguments                     | Meaning                                                                                                     |
|------------------|-------------------------------|-------------------------------------------------------------------------------------------------------------|
| `list` (default) | `[--own] [--language en\|zh]` | the catalogue (ID, title, category), or the own pictures; `*` marks the one shown                           |
| `apply`          | `ID [--own]`                  | show a catalogue picture, or an own one                                                                     |
| `upload`         | `FILE`                        | add an own picture from any image file; it is cropped to fill 480×200 and saved as a JPEG of about 77–96 KB |
| `delete`         | `ID`                          | remove an own picture                                                                                       |
| `time`           | `on\|off`                     | show the time over own pictures                                                                             |

`upload` needs Pillow: `pip install "anker-prime-driver[pictures]"`. Own
pictures need firmware that offers them; the catalogue needs firmware with
picture themes. A charger that asks for the picture over Bluetooth cannot be
served by this tool.

```bash
ankerprime clock-picture list --language zh
ankerprime clock-picture apply 31
ankerprime clock-picture upload ~/Pictures/beach.jpg
ankerprime clock-picture list --own
ankerprime clock-picture apply --own 101
ankerprime clock-picture time off
```

## Custom charging mode (250W)

Custom mode caps every output at a chosen power and lets each USB-C port allow
or block single fast-charge protocols. Up to four profiles (numbers 1–4) are
kept in the Anker account, shared with the Anker app; applying one writes its
limits to the charger. The Anker app shows custom mode only while the
account's custom mode setting (the app's Labs page) is on.

### `labs [on|off]`

Shows or switches the account's custom mode setting. Switching it off also
leaves custom mode.

### `custom-mode [ACTION]`

| Action           | Arguments                        | Meaning                                                                                                       |
|------------------|----------------------------------|---------------------------------------------------------------------------------------------------------------|
| `list` (default) |                                  | stored profiles; `*` marks the one in use                                                                     |
| `show`           | `NUMBER`                         | limits and allowed protocols per port                                                                         |
| `add`            | `NUMBER NAME [options]`          | store a new profile, starting from the Anker app's defaults (C1 100 W, C2 65 W, C3 45 W, C4 25 W, USB-A 15 W) |
| `edit`           | `NUMBER [--name NAME] [options]` | change a stored profile; when it is in use, the charger gets the new limits at once                           |
| `delete`         | `NUMBER`                         | remove a stored profile; leaves custom mode first if it is in use                                             |
| `apply`          | `NUMBER`                         | switch the charger to custom mode with that profile                                                           |
| `exit`           |                                  | leave custom mode; the charger returns to its previous mode                                                   |

Options of `add` and `edit`:

| Option                           | Meaning                                                                     |
|----------------------------------|-----------------------------------------------------------------------------|
| `--c1 W` … `--c4 W`, `--usb-a W` | limit of an output; 0 switches it off                                       |
| `--protocols PORT=LIST`          | protocols a port allows, replacing its list (repeatable; `c1=` allows none) |
| `--auto-exit on\|off`            | leave custom mode once every device is unplugged                            |

Limits: C1 15–140 W, C2–C4 15–100 W (120 W on mainland China accounts),
USB-A 15 or 24 W, at most 250 W in total. Protocols: `scp`, `ufcs`,
`pd_12v`, `pps_11v`, `pps_16v`, `pps_21v`; mainland China accounts use
`huawei` instead of `scp` and add `xiaomi`. A port allows at most three of PD
12V and the PPS ranges, and on mainland accounts not both PD 12V and Xiaomi.
Names are trimmed and limited to 20 characters; outside mainland China, SCP is
dropped from ports capped at 15–20 W, as in the Anker app. Every port drops
out for a few seconds when a profile is applied.

```bash
ankerprime labs on
ankerprime custom-mode add 1 Desk --c1 100 --c2 60 --usb-a 0
ankerprime custom-mode edit 1 --protocols c1=ufcs,pps_11v,pps_16v
ankerprime custom-mode show 1
ankerprime custom-mode apply 1
ankerprime custom-mode exit
```

## Energy counters

Chargers do not report energy; the library integrates it per port from the
live power readings (nothing is counted while no readings arrive). `monitor`
keeps the counters in `energy-<serial>.json`, so they continue across runs;
other commands only show them.

### `energy-reset [PORT ...] [-c NAME | --serial SERIAL]`

Sets the stored counters to zero: the given ports (`c1`…`c4`, `a1`, `a2`) or
all. With `-c`, `--serial` or a default charger it works offline; otherwise it
asks the account for its only cloud charger. A `monitor` running at the time
follows the reset within a minute.

## Exit codes

| Code | Meaning                                                        |
|------|----------------------------------------------------------------|
| 0    | success                                                        |
| 1    | error (connection, timeout, invalid argument, refused write …) |
| 2    | invalid command line                                           |
| 3    | authentication: sign in again, or for a 160W pass `--owner-id` |
| 130  | interrupted (Ctrl+C)                                           |
