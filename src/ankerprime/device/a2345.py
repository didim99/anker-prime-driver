"""Anker Prime Charger 250W (A2345) device logic over any frame channel.

On every connection it requests the full snapshot, the clock theme state and
a realtime window, and renews that window before it expires. Readings go
stale without telemetry; a silent connection is recycled. Writes wait for
their acknowledgement, resend once like the official app and are followed by
a debounced snapshot read.

Every realtime frame feeds the energy meter; the counters are saved to the
optional ``EnergyStore`` periodically and on ``disconnect``.

Telemetry carries port readings only: output switches, countdowns and other
settings changed elsewhere (another client, the app, the knob) are not pushed
over the cloud. They are picked up by a snapshot read after an acknowledgement
meant for another client, and by a periodic snapshot read.

With a ``ChargerAccount`` the device also reads the settings and custom
profiles the account keeps, on every connection and on ``refresh``. Switches
the account stores (custom mode, max compatibility, device identification)
are written to the account first, then to the charger, as the official app
does.

Clock pictures are downloaded by the charger itself; applying one waits for
its download report.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Self

from ankerprime.device.a2345_account import AccountFeatures
from ankerprime.device.a2345_pictures import PictureFeatures
from ankerprime.device.running import RunningCharger
from ankerprime.energy.recorder import EnergyRecorder
from ankerprime.energy.store import EnergyStore
from ankerprime.interfaces.account import ChargerAccount
from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import ChargingProfile
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, HourFormat, KnobDirection,
    OutputId, ScheduleSlot, ScreenTimeout)
from ankerprime.model.errors import (
    AnkerPrimeError, AuthenticationError,
    ChargerConnectionError, ChargerTimeoutError,
    InvalidParameterError, ProtocolError, WriteRejectedError)
from ankerprime.model.pictures import ClockPicture
from ankerprime.model.product import A2345
from ankerprime.model.state import ClockSchedule, DeviceInfo, Schedule
from ankerprime.protocol.a2345 import commands
from ankerprime.protocol.a2345.codes import (
    CHARGING_MODE_CODES, CLOCK_THEME_CODES, HOUR_FORMAT_CODES,
    KNOB_DIRECTION_CODES, SCREEN_TIMEOUT_CODES)
from ankerprime.protocol.a2345.commands import Command
from ankerprime.protocol.a2345.frame import MessageType, decode_frame
from ankerprime.protocol.a2345.messages import (
    RealtimeTelemetry, StatusSnapshot, decode_message)
from ankerprime.protocol.a2345.settings import (
    ClockState, PortSchedule, SettingField)
from ankerprime.protocol.a2345.tracker import StateTracker
from ankerprime.protocol.a2345.updates import (
    ChargingModeUpdate, ClockUpdate, CountdownUpdate, FirmwareVersionUpdate,
    OutputSwitchUpdate, PriorityUpdate, ScheduleUpdate, SettingsChange,
    SettingsUpdate, ThemeDownloadUpdate, ValueUpdate, WriteAcknowledgement)
from ankerprime.protocol.codes import decode_value, encode_value
from ankerprime.utils import Clock, copy_with, monotonic, redact, utc_now


logger = logging.getLogger(__name__)

WriteKey = tuple[int, OutputId | None, ScheduleSlot | None]



@dataclass(init=False)
class A2345Timings:
    """Timing rules, in seconds; the defaults follow the official app."""

    _realtime_refresh: float
    _stale_after: float
    _telemetry_deadline: float
    _settings_retry: float
    _settings_refresh: float | None
    _readback_delay: float
    _retry_initial: float
    _retry_max: float
    _energy_save: float
    _tick: float
    _write_waits: tuple[float, ...]
    _priority_waits: tuple[float, ...]
    _custom_waits: tuple[float, ...]
    _picture_download: float

    def __init__(self, *, realtime_refresh: float = 8.0,
                 stale_after: float = 15.0, telemetry_deadline: float = 45.0,
                 settings_retry: float = 10.0,
                 settings_refresh: float | None = 30.0,
                 readback_delay: float = 0.8,
                 retry_initial: float = 1.0, retry_max: float = 60.0,
                 energy_save: float = 60.0, tick: float = 1.0,
                 write_waits: Sequence[float] = (1.5, 3.0),
                 priority_waits: Sequence[float] = (4.0,),
                 custom_waits: Sequence[float] = (3.0, 4.0),
                 picture_download: float = 45.0):
        self._realtime_refresh = realtime_refresh
        self._stale_after = stale_after
        self._telemetry_deadline = telemetry_deadline
        self._settings_retry = settings_retry
        self._settings_refresh = settings_refresh
        self._readback_delay = readback_delay
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._energy_save = energy_save
        self._tick = tick
        self._write_waits = tuple(write_waits)
        self._priority_waits = tuple(priority_waits)
        self._custom_waits = tuple(custom_waits)
        self._picture_download = picture_download

    @property
    def realtime_refresh(self) -> float:
        """Renewal of the charger's 10-second realtime window."""

        return self._realtime_refresh

    @property
    def stale_after(self) -> float:
        return self._stale_after

    @property
    def telemetry_deadline(self) -> float:
        """Silence after which the connection is recycled."""

        return self._telemetry_deadline

    @property
    def settings_retry(self) -> float:
        """Minimum gap between repeated snapshot requests."""

        return self._settings_retry

    @property
    def settings_refresh(self) -> float | None:
        """Snapshot read period for changes made elsewhere (``None``: off)."""

        return self._settings_refresh

    @property
    def readback_delay(self) -> float:
        return self._readback_delay

    @property
    def retry_initial(self) -> float:
        return self._retry_initial

    @property
    def retry_max(self) -> float:
        return self._retry_max

    @property
    def energy_save(self) -> float:
        """Minimum gap between saves of changed energy counters."""

        return self._energy_save

    @property
    def tick(self) -> float:
        """Resolution of the maintenance loop."""

        return self._tick

    @property
    def write_waits(self) -> tuple[float, ...]:
        """Wait after each (re)send of an ordinary write."""

        return self._write_waits

    @property
    def priority_waits(self) -> tuple[float, ...]:
        """Priority writes re-allocate power, so they are sent once."""

        return self._priority_waits

    @property
    def custom_waits(self) -> tuple[float, ...]:
        """A custom profile re-negotiates every port; the charger answers late."""

        return self._custom_waits

    @property
    def picture_download(self) -> float:
        """Wait for the charger's report on a clock picture download."""

        return self._picture_download

    def retry_delay(self, failures: int) -> float:
        """Exponential delay before reconnect attempt ``failures``."""

        exponent = min(max(failures - 1, 0), 16)
        return min(self._retry_initial * float(1 << exponent), self._retry_max)

    def replace(self, **changes: Any) -> Self:
        """A copy with the given timings changed."""

        return copy_with(self, changes)

class _PendingWrite:
    """A write waiting for its acknowledgement.

    The future's result is the ack, or None when a theme status confirmed
    a write the charger does not acknowledge.
    """

    _command: Command
    _future: asyncio.Future[WriteAcknowledgement | None]

    def __init__(self, command: Command,
                 future: asyncio.Future[WriteAcknowledgement | None]):
        self._command = command
        self._future = future

    @property
    def command(self) -> Command:
        return self._command

    @property
    def future(self) -> asyncio.Future[WriteAcknowledgement | None]:
        return self._future


class A2345Device(RunningCharger, ChannelListener):
    """One A2345 behind a channel carrying plain FF09 frames."""

    _channel: Channel
    _info: DeviceInfo
    _account: ChargerAccount | None
    _timings: A2345Timings

    _tracker: StateTracker
    _available: bool

    _supervisor: asyncio.Task[None] | None
    _maintenance: asyncio.Task[None] | None
    _readback: asyncio.Task[None] | None

    _link_lost: asyncio.Event
    _stopping: bool
    _failures: int
    _opened_at: float
    _last_telemetry: float | None
    _last_trigger: float
    _settings_requested_at: float
    _settings_received_at: float
    _settings_fresh: bool

    _pending: list[_PendingWrite]
    _write_locks: dict[WriteKey, asyncio.Lock]

    _features: AccountFeatures
    _pictures: PictureFeatures

    def __init__(self, channel: Channel, info: DeviceInfo | None = None, *,
                 account: ChargerAccount | None = None,
                 energy_store: EnergyStore | None = None,
                 timings: A2345Timings | None = None,
                 clock: Clock | None = None):
        info = info or DeviceInfo()
        timings = timings or A2345Timings()
        energy = EnergyRecorder((spec.port for spec in A2345.ports),
                                energy_store, label=redact(info.serial),
                                save_interval=timings.energy_save)
        super().__init__(A2345, energy, clock or utc_now)
        self._channel = channel
        self._info = info
        self._account = account
        self._timings = timings
        self._tracker = StateTracker(A2345)
        self._available = False
        self._supervisor = None
        self._maintenance = None
        self._readback = None
        self._link_lost = asyncio.Event()
        self._stopping = False
        self._failures = 0
        self._opened_at = 0.0
        self._last_telemetry = None
        self._last_trigger = 0.0
        self._settings_requested_at = 0.0
        self._settings_received_at = 0.0
        self._settings_fresh = False
        self._pending = []
        self._write_locks = {}
        self._features = AccountFeatures(
            account, A2345.custom_mode, write=self._write,
            device_settings=lambda: self._tracker.settings,
            publish=self._publish_state, label=lambda: self._label,
            custom_waits=self._timings.custom_waits)
        self._pictures = PictureFeatures(
            self._features.require, write=self._write,
            device_settings=lambda: self._tracker.settings,
            download_timeout=self._timings.picture_download)
        self._publish_state()

    @property
    def connected(self) -> bool:
        return self._channel.is_open

    def on_message(self, data: bytes) -> None:
        try:
            frame = decode_frame(data)
            message = decode_message(frame)
        except ProtocolError as error:
            logger.debug('ignored frame from %s: %s',
                         self._label, error)
            return
        if not isinstance(message, RealtimeTelemetry):
            logger.debug('received %#06x from %s', frame.message_type,
                         self._label)
        if isinstance(message, WriteAcknowledgement):
            self._resolve(message)
            return
        if not self._tracker.apply(message, self._clock()):
            return
        if isinstance(message, SettingsChange):
            self._confirm(message)
            for update in message.updates:
                if isinstance(update, ThemeDownloadUpdate):
                    self._pictures.on_download(update)
        if isinstance(message, RealtimeTelemetry):
            self._record_energy(message)
        if isinstance(message, (RealtimeTelemetry, StatusSnapshot)):
            self._on_telemetry(message)
        if isinstance(message, SettingsChange) and any(
                isinstance(update, FirmwareVersionUpdate)
                for update in message.updates):
            # New firmware may change the capability bits; read everything.
            self._spawn(self._request_settings())
        self._publish_state()

    def on_closed(self, error: Exception | None) -> None:
        if not self._stopping:
            if error is not None:
                logger.debug('link to %s lost: %s', self._label,
                             error)
            self._link_lost.set()

    async def connect(self) -> None:
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._stopping = False
        self._last_error = None
        self._failures = 0
        if await self._energy.load():
            self._publish_state()
        await self._open_link()
        self._supervisor = asyncio.create_task(
            self._supervise(), name='ankerprime-a2345-supervisor')

    async def disconnect(self) -> None:
        self._stopping = True
        await self._cancel_tasks(self._supervisor, self._maintenance,
                                 self._readback)
        self._supervisor = None
        self._maintenance = None
        self._readback = None
        await self._channel.close()
        self._energy.interrupt()
        self._end_link(ChargerConnectionError('disconnected'))
        await self._save_energy()

    async def refresh(self) -> None:
        if not self._channel.is_open:
            raise ChargerConnectionError('charger is not connected')
        await self._request_settings()
        if self._account is not None:
            self._spawn(self._features.load())

    async def _set_output(self, output: OutputId, on: bool) -> None:
        await self._write(commands.set_output(output, on),
                          [OutputSwitchUpdate(output, on)])

    async def _start_countdown(self, output: OutputId, seconds: int) -> None:
        countdown = self._product.countdown
        if countdown is None or not countdown.contains(seconds):
            raise InvalidParameterError(f'countdown out of range: {seconds}')
        update = CountdownUpdate(output, True, seconds, seconds)
        await self._write(commands.set_countdown(output, True, seconds),
                          [update])

    async def _cancel_countdown(self, output: OutputId) -> None:
        # The official app echoes the reported total when cancelling.
        total = 0
        settings = self._tracker.settings
        if settings is not None and output in settings.outputs:
            total = settings.outputs[output].countdown_total
        update = CountdownUpdate(output, False, 0, 0)
        await self._write(commands.set_countdown(output, False, total),
                          [update])

    async def _set_schedule(self, output: OutputId, slot: ScheduleSlot,
                            schedule: Schedule) -> None:
        weekdays = int(schedule.weekdays) & 0x7F
        reported = PortSchedule(int(schedule.enabled), schedule.hour,
                                schedule.minute, weekdays)
        await self._write(commands.set_schedule(output, slot, schedule),
                          [ScheduleUpdate(output, slot, reported)])

    async def _set_charging_mode(self, mode: ChargingMode) -> None:
        if mode not in self._product.charging_modes:
            raise InvalidParameterError(f'charging mode not offered: {mode}')
        code = encode_value(CHARGING_MODE_CODES, mode)
        await self._write(commands.set_charging_mode(mode),
                          [ChargingModeUpdate(code)])

    async def _set_preferred_outputs(self, outputs: Collection[OutputId]
                                     ) -> None:
        command = commands.set_preferred_outputs(outputs)
        bitmap = command.fields[0xA2][0]
        await self._write(command, [PriorityUpdate(bitmap=bitmap)],
                          waits=self._timings.priority_waits)

    async def _set_brightness(self, percent: int) -> None:
        brightness = self._product.brightness
        if brightness is None or not brightness.contains(percent):
            raise InvalidParameterError(f'brightness out of range: {percent}')
        update = ValueUpdate(SettingField.BRIGHTNESS, percent)
        await self._write(commands.set_brightness(percent), [update])

    async def _set_screen_timeout(self, timeout: ScreenTimeout) -> None:
        code = encode_value(SCREEN_TIMEOUT_CODES, timeout)
        update = ValueUpdate(SettingField.SCREEN_TIMEOUT, code)
        await self._write(commands.set_screen_timeout(timeout), [update])

    async def _set_knob_direction(self, direction: KnobDirection) -> None:
        code = encode_value(KNOB_DIRECTION_CODES, direction)
        update = ValueUpdate(SettingField.KNOB_DIRECTION, code)
        await self._write(commands.set_knob_direction(direction), [update])

    async def _set_hour_format(self, hour_format: HourFormat) -> None:
        code = encode_value(HOUR_FORMAT_CODES, hour_format)
        update = ValueUpdate(SettingField.HOUR_FORMAT, code)
        await self._write(commands.set_hour_format(hour_format), [update])

    async def _set_clock(self, enabled: bool, theme: ClockTheme | None
                         ) -> None:
        if theme in (ClockTheme.PICTURE, ClockTheme.CUSTOM):
            raise InvalidParameterError(
                'pictures are chosen with apply_clock_picture')
        settings = self._tracker.settings
        clock = settings.clock if settings is not None else None
        if theme is None and clock is not None:
            theme = decode_value(CLOCK_THEME_CODES, clock.theme_code)
        if theme is None:
            raise InvalidParameterError('current clock theme unknown')
        holiday = clock.holiday_enabled if clock is not None else False
        if clock is not None and theme in (ClockTheme.PICTURE,
                                           ClockTheme.CUSTOM):
            # Switching resends the current theme, so the picture is needed.
            command, expected = await self._pictures.switch_command(
                enabled, clock, theme)
        else:
            url = commands.builtin_theme_url(theme)
            command = commands.set_clock_theme(enabled, theme, url=url)
            expected = ClockState(enabled, holiday and enabled,
                                  CLOCK_THEME_CODES[theme])
        await self._write(command, [ClockUpdate(expected)],
                          include_theme=True)

    async def _set_clock_schedule(self, schedule: ClockSchedule) -> None:
        await self._write(commands.set_clock_schedule(schedule))

    async def _set_holiday_theme(self, enabled: bool) -> None:
        await self._write(commands.set_holiday_theme(enabled))

    async def _set_max_compatibility(self, enabled: bool) -> None:
        await self._features.set_max_compatibility(enabled)

    async def _set_device_identification(self, enabled: bool) -> None:
        await self._features.set_device_identification(enabled)

    async def _set_ufcs(self, enabled: bool) -> None:
        update = ValueUpdate(SettingField.UFCS, int(enabled))
        await self._write(commands.set_ufcs(enabled), [update])

    async def _apply_custom_profile(self, profile: ChargingProfile) -> None:
        await self._features.apply_profile(profile)

    async def _exit_custom_mode(self) -> None:
        await self._features.exit_mode()

    async def _set_custom_mode_enabled(self, enabled: bool) -> None:
        await self._features.set_enabled(enabled)

    async def _save_custom_profile(self, profile: ChargingProfile) -> None:
        await self._features.save(profile)

    async def _delete_custom_profile(self, number: int) -> None:
        await self._features.delete(number)

    async def _reload_custom_profiles(self) -> None:
        await self._features.reload()

    async def _clock_pictures(self, language: str
                              ) -> tuple[ClockPicture, ...]:
        return await self._pictures.catalogue(language)

    async def _own_clock_pictures(self) -> tuple[ClockPicture, ...]:
        return await self._pictures.own()

    async def _apply_clock_picture(self, picture: ClockPicture) -> None:
        await self._pictures.apply(picture)

    async def _upload_clock_picture(self, jpeg: bytes) -> None:
        await self._pictures.upload(jpeg)

    async def _delete_clock_picture(self, picture_id: int) -> None:
        await self._pictures.delete(picture_id)

    async def _set_picture_time(self, enabled: bool) -> None:
        await self._pictures.set_time(enabled)

    async def _open_link(self) -> None:
        """Open the channel and send the initial requests."""

        self._link_lost = asyncio.Event()
        self._channel.set_listener(self)
        await self._channel.open()
        now = monotonic()
        self._opened_at = now
        self._last_telemetry = None
        self._settings_fresh = False
        try:
            await self._request_settings()
            await self._send(commands.realtime_trigger())
        except ChargerConnectionError:
            await self._channel.close()
            raise
        self._last_trigger = now
        self._maintenance = asyncio.create_task(
            self._maintain(self._link_lost), name='ankerprime-a2345-maintenance')
        if self._account is not None:
            self._spawn(self._features.load())

    async def _supervise(self) -> None:
        """Reopen the link after every loss, with growing delays."""

        while not self._stopping:
            await self._link_lost.wait()
            await self._close_link()
            while not self._stopping:
                self._failures += 1
                await asyncio.sleep(self._timings.retry_delay(self._failures))
                try:
                    await self._open_link()
                    break
                except AuthenticationError as error:
                    logger.warning('reconnecting %s needs new credentials',
                                   self._label)
                    self._last_error = error
                    self._publish_state()
                    return
                except AnkerPrimeError as error:
                    logger.debug('reconnecting %s failed: %s',
                                 self._label, error)
                except Exception:
                    logger.exception('unexpected error reconnecting %s',
                                     self._label)

    async def _close_link(self) -> None:
        maintenance = self._maintenance
        self._maintenance = None
        if maintenance is not None and maintenance is not asyncio.current_task():
            maintenance.cancel()
        await self._channel.close()
        self._end_link(ChargerConnectionError('connection to charger lost'))

    def _end_link(self, error: ChargerConnectionError) -> None:
        for pending in self._pending:
            if not pending.future.done():
                pending.future.set_exception(error)
        self._pictures.fail(error)
        self._settings_fresh = False
        self._available = False
        self._publish_state()

    async def _maintain(self, link_lost: asyncio.Event) -> None:
        """Renew the realtime window, track staleness, enforce the deadline."""

        timings = self._timings
        while not link_lost.is_set():
            await asyncio.sleep(timings.tick)
            now = monotonic()
            reference = self._last_telemetry or self._opened_at
            if now - reference >= timings.telemetry_deadline:
                logger.debug('no telemetry from %s; recycling the link',
                             self._label)
                link_lost.set()
                return
            fresh = (self._last_telemetry is not None
                     and now - self._last_telemetry <= timings.stale_after)
            if fresh != self._available:
                self._available = fresh
                self._publish_state()
            if self._settings_due(now):
                self._spawn(self._request_settings())
            self._save_energy_if_due(now)
            if now - self._last_trigger >= timings.realtime_refresh:
                self._last_trigger = now
                try:
                    await self._send(commands.realtime_trigger())
                except ChargerConnectionError:
                    return

    def _on_telemetry(self, message: RealtimeTelemetry | StatusSnapshot
                      ) -> None:
        now = monotonic()
        self._last_telemetry = now
        self._failures = 0
        self._available = True
        if isinstance(message, StatusSnapshot):
            self._settings_fresh = True
            self._settings_received_at = now
            return
        # The snapshot reply can be lost; without it no write is possible.
        waited = now - self._settings_requested_at
        if not self._settings_fresh and waited >= self._timings.settings_retry:
            self._spawn(self._request_settings())

    def _settings_due(self, now: float) -> bool:
        """Whether the periodic snapshot read is due."""

        period = self._timings.settings_refresh
        if period is None or not self._settings_fresh:
            return False
        last = max(self._settings_received_at, self._settings_requested_at)
        return now - last >= period

    def _record_energy(self, message: RealtimeTelemetry) -> None:
        powers = {port: reading.power
                  for port, reading in message.ports.items()}
        self._energy.record(monotonic(), powers)

    async def _request_settings(self) -> None:
        self._settings_requested_at = monotonic()
        await self._send(commands.status_snapshot())
        await self._send(commands.theme_status())

    async def _write(self, command: Command,
                     expected: Sequence[SettingsUpdate] = (), *,
                     waits: Sequence[float] | None = None,
                     include_theme: bool = False) -> None:
        """Send a write, wait for its ack, then apply the expected change.

        Writes to the same target are serialised; others run concurrently.
        """

        if waits is None:
            waits = self._timings.write_waits
        key = (command.message_type, command.output, command.slot)
        lock = self._write_locks.setdefault(key, asyncio.Lock())
        async with lock:
            self._require_ready()
            future = asyncio.get_running_loop().create_future()
            pending = _PendingWrite(command, future)
            self._pending.append(pending)
            try:
                ack = await self._await_ack(pending, waits)
            finally:
                self._pending.remove(pending)
                if self._channel.is_open:
                    self._schedule_readback(include_theme)
        if ack is not None and ack.rejected:
            raise WriteRejectedError(ack.status or 0)
        if expected:
            change = SettingsChange(command.message_type, expected)
            self._tracker.apply(change, self._clock())
            self._publish_state()

    async def _await_ack(self, pending: _PendingWrite,
                         waits: Sequence[float]
                         ) -> WriteAcknowledgement | None:
        for wait in waits:
            await self._send(pending.command)
            try:
                return await asyncio.wait_for(asyncio.shield(pending.future),
                                              wait)
            except TimeoutError:
                continue
        raise ChargerTimeoutError('charger did not acknowledge the write')

    def _resolve(self, ack: WriteAcknowledgement) -> None:
        for pending in self._pending:
            if not pending.future.done() and pending.command.matches(ack):
                pending.future.set_result(ack)
                return
        if ack.message_type == MessageType.REALTIME_ACK:
            # Answers the realtime trigger: nothing changed.
            return
        logger.debug('ack %#06x (status %s) from %s matches no pending write',
                     ack.message_type, ack.status, self._label)
        # Acks reach every client; another one changed something.
        if self._channel.is_open and not ack.rejected:
            self._schedule_readback(include_theme=True)

    def _confirm(self, change: SettingsChange) -> None:
        """Complete writes the charger answers with a theme status."""

        for pending in self._pending:
            if (not pending.future.done()
                    and pending.command.confirmed_by(change)):
                pending.future.set_result(None)

    def _schedule_readback(self, include_theme: bool) -> None:
        """Read the snapshot once a burst of writes settles."""

        if self._readback is not None:
            self._readback.cancel()
        self._readback = asyncio.create_task(self._read_back(include_theme))

    async def _read_back(self, include_theme: bool) -> None:
        await asyncio.sleep(self._timings.readback_delay)
        try:
            await self._send(commands.status_snapshot())
            if include_theme:
                await self._send(commands.theme_status())
        except ChargerConnectionError:
            pass

    async def _send(self, command: Command) -> None:
        logger.debug('sending %#06x to %s', command.message_type, self._label)
        timestamp = self._timestamp()
        try:
            await self._channel.send(command.encode(timestamp))
        except ChargerConnectionError:
            self._link_lost.set()
            raise

    def _require_ready(self) -> None:
        if not self._channel.is_open or not self._settings_fresh:
            raise ChargerConnectionError('charger is not ready for writes')

    @property
    def _label(self) -> str:
        return redact(self._info.serial)

    def _task_failed(self, error: BaseException) -> bool:
        # A lost link is handled by the supervisor.
        return isinstance(error, ChargerConnectionError)

    def _publish_state(self) -> None:
        unavailable: tuple[Capability, ...] = ()
        if self._account is None:
            unavailable = (Capability.CUSTOM_PROFILES,
                           Capability.CLOCK_PICTURE,
                           Capability.OWN_CLOCK_PICTURES)
        state = self._tracker.build(info=self._info,
                                    available=self._available,
                                    energy=self._energy.counters,
                                    account=self._features.settings,
                                    profiles=self._features.profiles,
                                    unavailable=unavailable)
        self._publish(state)

