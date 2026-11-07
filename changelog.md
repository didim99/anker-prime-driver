# Changelog

All notable changes to `anker-prime-driver`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning while in development (major version 0): the minor version grows
with big changes (a new module, subsystem or charger model), the patch version
with every other change; documentation-only changes keep the version.
0.7.3 is the first version published on PyPI; earlier numbers were assigned
to the development history afterwards.

## [Unreleased]

### Added
- 250W custom charging mode (experimental):
  - `ChargingProfile` with per-output limits and fast-charge protocols;
    `product.custom_mode` (`CustomModeRules`) with the limits, the Anker
    app's defaults and validation, including mainland China limits;
  - `Charger.apply_custom_profile()` and `exit_custom_mode()`;
  - profiles stored in the Anker account: `save_custom_profile()`,
    `delete_custom_profile()`, `reload_custom_profiles()`, and the account's
    custom mode setting with `set_custom_mode_enabled()`;
  - state: `settings.active_profile`, `custom_profiles`,
    `custom_mode_enabled`; new capability `custom_profiles`.
- `ChargerAccount` interface for charger settings kept in the account;
  `CloudAccount` provides one per charger (`mini_power` API).
- `CloudClient.request()` for JSON requests with a body; `CloudAccount.call()`
  runs one with the account's re-login.
- CLI: `custom-mode list|show|add|edit|delete|apply|exit` and `labs`;
  `status` shows the profile in use and the custom mode setting.
- 250W clock pictures (experimental):
  - the cloud catalogue (`clock_pictures()`, English or Chinese titles) and
    the account's own pictures (`own_clock_pictures()`, at most eight) as
    `ClockPicture`;
  - `apply_clock_picture()` waits for the charger's download report
    (`PictureTransferError`, `ChargerTimeoutError`); own pictures are
    re-listed first for a fresh link;
  - `upload_clock_picture()`, `delete_clock_picture()`, `set_picture_time()`;
  - state: `clock.picture_id`, `clock.picture_time`; capabilities
    `clock_picture` (now experimental) and `own_clock_pictures`.
- `ankerprime.pictures.prepare_picture()` and the `[pictures]` extra
  (Pillow): fill-crop to 480×200 and a JPEG in the official size window.
- `HttpClient.put()` for signed uploads; `CloudClient.upload()`.
- CLI: `clock-picture list|apply|upload|delete|time`; `status` shows the
  shown picture and the time setting.

### Changed
- 250W: max compatibility and device identification exclude each other;
  switching one on first switches the other off. With an account, both are
  written to the account first, then to the charger, as the official app
  does.
- 250W: switching the clock while a picture is shown resends that picture
  instead of failing; `set_clock()` with a picture theme raises
  `InvalidParameterError` (use `apply_clock_picture()`).

## [0.7.3] - 2026-11-07

First release on PyPI.

### Fixed
- 250W: a background task canceled before it started (for example by
  `disconnect()` right after a settings read was queued) left its coroutine
  never awaited; unexpected task errors are now logged, and a new energy save
  no longer starts while one is running.

### Changed
- Development status is Alpha; readme prepared for PyPI.

## [0.7.2] - 2026-11-07

### Added
- `ble_charger(ble_device_callback=...)`: supplies the newest `BLEDevice`
  before each connection attempt (Home Assistant:
  `bluetooth.async_ble_device_from_address`), so a Bluetooth proxy taking
  over is used.
- `ankerprime.api.ble` exports `A2687Timings`, `DeviceCallback`,
  `advertisement_match` and `new_client_id`.

### Documentation
- Readme restructured; new [CLI reference](docs/cli.md) and
  [library overview](docs/library.md) with usage examples.

## [0.7.1] - 2026-11-07

### Added
- CLI saved chargers: `charger list|add|remove|default`, `-c/--charger NAME`
  on every charger command and on `energy-reset`; commands without a
  selection use the default charger. A saved Bluetooth charger records its
  serial on the first connection.

### Changed
- CLI `--country` must be a two-letter code.

## [0.7.0] - 2026-11-07

### Added
- Anker Prime 160W (A2687) over Bluetooth LE, ported from Charker:
  - protocol: frames, typed TLVs, commands, report decoding, state tracker;
  - session: P-256 key agreement, AES-GCM, user auth with an optional owner
    account ID (`A2687Session`);
  - transport: `BleTransport` on bleak and bleak-retry-connector, scanning
    with `discover_chargers()`;
  - device: `A2687Device` with a kept connection or on-demand reads,
    reconnects, writes with read-back, energy counters by serial;
  - `ble_charger()` factory (`ankerprime.api.ble`, `[ble]` extra).
- `AccountRefusedError` for a 160W refusing the user identity.
- CLI: `scan`, `--ble ADDRESS`, `--owner-id`, `--country`; a Bluetooth
  identity kept in the config folder.

### Changed
- The FF09 envelope and the wire-code helpers are shared by both protocols.
- The CLI stops waiting for a charger's state as soon as it gives up.

## [0.6.3] - 2026-11-04

### Changed
- Packaging for the Home Assistant OS wheel route.

## [0.6.2] - 2026-11-04

### Added
- CLI colours (`--color auto|always|never`, `NO_COLOR`, `FORCE_COLOR`).

## [0.6.1] - 2026-11-04

### Added
- Energy recorder shared by devices: periodic saves and merging of counts
  from several processes; resets with markers that running monitors follow.
- `Charger.reset_energy()` and the CLI `energy-reset` command.

## [0.6.0] - 2026-11-04

### Added
- Per-port energy counters integrated from the live power readings, with a
  document store for persistence.

## [0.5.2] - 2026-11-04

### Added
- CLI `monitor` shows how long it has been running.

## [0.5.1] - 2026-11-04

### Added
- CLI `monitor` redraws in place, shows the last update and its age, and
  shows the full state with `--detail` only.

### Changed
- 250W: settings changed elsewhere are read back, and settings are re-read
  every 30 s, since the cloud does not push them.
- 250W capabilities verified on real hardware.

## [0.5.0] - 2026-11-04

### Added
- `ankerprime` command-line tool: account, reading and control commands.

## [0.4.1] - 2026-11-04

### Fixed
- Cloud connection and device fixes.

## [0.4.0] - 2026-11-04

### Added
- `A2345Device` (Anker Prime Charger 250W) over the Anker cloud MQTT
  connection; `CloudAccount` as the entry point.

## [0.3.0] - 2026-11-04

### Added
- Anker cloud client: sign-in, regions, bound devices, MQTT credentials.
- Document storage with JSON files.

## [0.2.2] - 2026-11-04

### Added
- 250W protocol: commands, settings and state tracker.

## [0.2.1] - 2026-11-04

### Added
- 250W protocol: message decoders.

## [0.2.0] - 2026-11-04

### Added
- 250W protocol: frame codec and stream reassembler.

## [0.1.2] - 2026-11-04

### Changed
- Model refinements.

## [0.1.1] - 2026-11-03

### Changed
- Model refinements.

## [0.1.0] - 2026-11-03

### Added
- Shared model (products, capabilities, state, errors) and interfaces.

## [0.0.2] - 2026-11-03

### Changed
- Project metadata.

## [0.0.1] - 2026-11-03

### Added
- Project skeleton and tooling.

[0.7.3]: https://pypi.org/project/anker-prime-driver/0.7.3/
