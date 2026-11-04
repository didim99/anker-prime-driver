"""Anker Prime Charger 250W (A2345) device logic over any frame channel.

On every connection it requests the full snapshot, the clock theme state and
a realtime window, and renews that window before it expires. Readings go
stale without telemetry; a silent connection is recycled. Writes wait for
their acknowledgement, resend once like the official app and are followed by
a debounced snapshot read.

Telemetry carries port readings only: output switches, countdowns and other
settings changed elsewhere (another client, the app, the knob) are not pushed
over the cloud. They are picked up by a snapshot read after an acknowledgement
meant for another client, and by a periodic snapshot read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from ankerprime.device.base import BaseCharger
from ankerprime.interfaces.channel import Channel, ChannelListener
from ankerprime.model.capabilities import Capability
from ankerprime.model.enums import (
    ChargingMode, ClockTheme, HourFormat, KnobDirection,
    OutputId, ScheduleSlot, ScreenTimeout)
from ankerprime.model.errors import (
    AnkerPrimeError, AuthenticationError, ChargerConnectionError,
    ChargerTimeoutError, InvalidParameterError, ProtocolError,
    UnsupportedCapabilityError, WriteRejectedError)
from ankerprime.model.product import A2345
from ankerprime.model.state import ClockSchedule, DeviceInfo, Schedule
from ankerprime.protocol.a2345 import commands
from ankerprime.protocol.a2345.codes import (
    CHARGING_MODE_CODES, CLOCK_THEME_CODES, HOUR_FORMAT_CODES,
    KNOB_DIRECTION_CODES, SCREEN_TIMEOUT_CODES, decode_value, encode_value)
from ankerprime.protocol.a2345.commands import Command
from ankerprime.protocol.a2345.frame import decode_frame
from ankerprime.protocol.a2345.messages import (
    RealtimeTelemetry, StatusSnapshot, decode_message)
from ankerprime.protocol.a2345.settings import (
    ChargingModeUpdate, ClockState, ClockUpdate, CountdownUpdate,
    FirmwareVersionUpdate, OutputSwitchUpdate, PortSchedule,
    PriorityUpdate, ScheduleUpdate, SettingField, SettingsChange,
    SettingsUpdate, ValueUpdate, WriteAcknowledgement)
from ankerprime.protocol.a2345.tracker import StateTracker
from ankerprime.utils import Clock, redact, utc_now


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
    _tick: float
    _write_waits: tuple[float, ...]
    _priority_waits: tuple[float, ...]

    def __init__(self, *, realtime_refresh: float = 8.0,
                 stale_after: float = 15.0, telemetry_deadline: float = 45.0,
                 settings_retry: float = 10.0,
                 settings_refresh: float | None = 30.0,
                 readback_delay: float = 0.8,
                 retry_initial: float = 1.0, retry_max: float = 60.0,
                 tick: float = 1.0,
                 write_waits: Sequence[float] = (1.5, 3.0),
                 priority_waits: Sequence[float] = (4.0,)):
        self._realtime_refresh = realtime_refresh
        self._stale_after = stale_after
        self._telemetry_deadline = telemetry_deadline
        self._settings_retry = settings_retry
        self._settings_refresh = settings_refresh
        self._readback_delay = readback_delay
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._tick = tick
        self._write_waits = tuple(write_waits)
        self._priority_waits = tuple(priority_waits)

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

    def retry_delay(self, failures: int) -> float:
        """Exponential delay before reconnect attempt ``failures``."""

        exponent = min(max(failures - 1, 0), 16)
        return min(self._retry_initial * float(1 << exponent), self._retry_max)


class _PendingWrite:
    """A write waiting for its acknowledgement."""

    _command: Command
    _future: asyncio.Future[WriteAcknowledgement]

    def __init__(self, command: Command,
                 future: asyncio.Future[WriteAcknowledgement]):
        self._command = command
        self._future = future

    @property
    def command(self) -> Command:
        return self._command

    @property
    def future(self) -> asyncio.Future[WriteAcknowledgement]:
        return self._future


class A2345Device(BaseCharger, ChannelListener):
    """One A2345 behind a channel carrying plain FF09 frames."""

    _channel: Channel
    _info: DeviceInfo
    _timings: A2345Timings
    _clock: Clock

    _tracker: StateTracker
    _available: bool

    _supervisor: asyncio.Task[None] | None
    _maintenance: asyncio.Task[None] | None
    _readback: asyncio.Task[None] | None
    _tasks: set[asyncio.Task[Any]]

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

    def __init__(self, channel: Channel, info: DeviceInfo | None = None, *,
                 timings: A2345Timings | None = None,
                 clock: Clock | None = None):
        super().__init__(A2345)
        self._channel = channel
        self._info = info or DeviceInfo()
        self._timings = timings or A2345Timings()
        self._clock = clock or utc_now
        self._tracker = StateTracker(A2345)
        self._available = False
        self._supervisor = None
        self._maintenance = None
        self._readback = None
        self._tasks = set()
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
        self._publish_state()

    @property
    def connected(self) -> bool:
        return self._channel.is_open

    def on_message(self, data: bytes) -> None:
        try:
            message = decode_message(decode_frame(data))
        except ProtocolError as error:
            logger.debug('ignored frame from %s: %s',
                         redact(self._info.serial), error)
            return
        if isinstance(message, WriteAcknowledgement):
            self._resolve(message)
            return
        if not self._tracker.apply(message, self._clock()):
            return
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
                logger.debug('link to %s lost: %s', redact(self._info.serial),
                             error)
            self._link_lost.set()

    async def connect(self) -> None:
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._stopping = False
        self._last_error = None
        self._failures = 0
        await self._open_link()
        self._supervisor = asyncio.create_task(
            self._supervise(), name='ankerprime-a2345-supervisor')

    async def disconnect(self) -> None:
        self._stopping = True
        tasks = [task for task in (self._supervisor, self._maintenance,
                                   self._readback, *self._tasks)
                 if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor = None
        self._maintenance = None
        self._readback = None
        self._tasks.clear()
        await self._channel.close()
        self._end_link(ChargerConnectionError('disconnected'))

    async def refresh(self) -> None:
        if not self._channel.is_open:
            raise ChargerConnectionError('charger is not connected')
        await self._request_settings()

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
        settings = self._tracker.settings
        clock = settings.clock if settings is not None else None
        if theme is None and clock is not None:
            theme = decode_value(CLOCK_THEME_CODES, clock.theme_code)
        if theme is None:
            raise InvalidParameterError('current clock theme unknown')
        if theme in (ClockTheme.PICTURE, ClockTheme.CUSTOM):
            raise UnsupportedCapabilityError(Capability.CLOCK_PICTURE)
        url = commands.builtin_theme_url(theme)
        holiday = clock.holiday_enabled if clock is not None else False
        expected = ClockState(enabled, holiday and enabled,
                              CLOCK_THEME_CODES[theme])
        await self._write(commands.set_clock_theme(enabled, theme, url=url),
                          [ClockUpdate(expected)], include_theme=True)

    async def _set_clock_schedule(self, schedule: ClockSchedule) -> None:
        await self._write(commands.set_clock_schedule(schedule))

    async def _set_holiday_theme(self, enabled: bool) -> None:
        await self._write(commands.set_holiday_theme(enabled))

    async def _set_max_compatibility(self, enabled: bool) -> None:
        update = ValueUpdate(SettingField.MAX_COMPATIBILITY, int(enabled))
        await self._write(commands.set_max_compatibility(enabled), [update])

    async def _set_device_identification(self, enabled: bool) -> None:
        update = ValueUpdate(SettingField.DEVICE_IDENTIFICATION, int(enabled))
        await self._write(commands.set_device_identification(enabled),
                          [update])

    async def _set_ufcs(self, enabled: bool) -> None:
        update = ValueUpdate(SettingField.UFCS, int(enabled))
        await self._write(commands.set_ufcs(enabled), [update])

    async def _open_link(self) -> None:
        """Open the channel and send the initial requests."""

        self._link_lost = asyncio.Event()
        self._channel.set_listener(self)
        await self._channel.open()
        now = _monotonic()
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
                                   redact(self._info.serial))
                    self._last_error = error
                    self._publish_state()
                    return
                except AnkerPrimeError as error:
                    logger.debug('reconnecting %s failed: %s',
                                 redact(self._info.serial), error)
                except Exception:
                    logger.exception('unexpected error reconnecting %s',
                                     redact(self._info.serial))

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
        self._settings_fresh = False
        self._available = False
        self._publish_state()

    async def _maintain(self, link_lost: asyncio.Event) -> None:
        """Renew the realtime window, track staleness, enforce the deadline."""

        timings = self._timings
        while not link_lost.is_set():
            await asyncio.sleep(timings.tick)
            now = _monotonic()
            reference = self._last_telemetry or self._opened_at
            if now - reference >= timings.telemetry_deadline:
                logger.debug('no telemetry from %s; recycling the link',
                             redact(self._info.serial))
                link_lost.set()
                return
            fresh = (self._last_telemetry is not None
                     and now - self._last_telemetry <= timings.stale_after)
            if fresh != self._available:
                self._available = fresh
                self._publish_state()
            if self._settings_due(now):
                self._spawn(self._request_settings())
            if now - self._last_trigger >= timings.realtime_refresh:
                self._last_trigger = now
                try:
                    await self._send(commands.realtime_trigger())
                except ChargerConnectionError:
                    return

    def _on_telemetry(self, message: RealtimeTelemetry | StatusSnapshot
                      ) -> None:
        now = _monotonic()
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

    async def _request_settings(self) -> None:
        self._settings_requested_at = _monotonic()
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
        if ack.rejected:
            raise WriteRejectedError(ack.status or 0)
        if expected:
            change = SettingsChange(command.message_type, expected)
            self._tracker.apply(change, self._clock())
            self._publish_state()

    async def _await_ack(self, pending: _PendingWrite,
                         waits: Sequence[float]) -> WriteAcknowledgement:
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
        # Acks reach every client; another one changed something.
        if self._channel.is_open and not ack.rejected:
            self._schedule_readback(include_theme=True)

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
        timestamp = int(self._clock().timestamp())
        try:
            await self._channel.send(command.encode(timestamp))
        except ChargerConnectionError:
            self._link_lost.set()
            raise

    def _require_ready(self) -> None:
        if not self._channel.is_open or not self._settings_fresh:
            raise ChargerConnectionError('charger is not ready for writes')

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(_quietly(coroutine))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _publish_state(self) -> None:
        state = self._tracker.build(info=self._info,
                                    available=self._available)
        self._publish(state)


async def _quietly(coroutine: Coroutine[Any, Any, None]) -> None:
    try:
        await coroutine
    except ChargerConnectionError:
        pass


def _monotonic() -> float:
    return asyncio.get_running_loop().time()
