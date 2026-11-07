"""Anker Prime 160W (A2687) device logic over an ``A2687Session``.

Rules from Charker, taken on trust (no 160W was available here):

- After the negotiation: read-all, bind probe and realtime trigger, 0.4 s
  apart. The charger then reports about once a second.
- While connected: the bind probe every 6 s, a read-all every second time.
- Readings go stale 20 s after the last report; a link without reports for
  20 s is recycled. Lost links are reopened with growing delays (2 s ... 30 s).
- A link that never reports after the charger refused the user identity
  stops reconnecting with ``AccountRefusedError``: the charger is bound to
  another account, whose ID must be given as the owner ID.
- Writes wait for the reply with the same opcode (6 s), check its status
  and read everything back. Writes are serialised.

Energy counters are stored under the serial, which the first negotiation
reveals when the caller did not know it.

Custom mode is ``0x0206`` mode 2 with the C1-C3 limits; the read-back shows
whether the charger took it. A screen picture goes over in 156-byte slices,
pacing as Charker: the firmware answers every tenth slice and the last one,
and the sender waits for each answer. Polling stands down meanwhile. The
picture shown is read from ``E1`` before and after, the only witness that
the screen changed.

A Bluetooth charger accepts one connection at a time, so the official app is
locked out while it is held. With ``keep_connection=False`` the device
connects every ``update_interval`` (and for each command), reads the state
and disconnects after a short linger. Energy between such reads is
interpolated when they are at most two minutes apart (the meter's gap rule).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self

from ankerprime.device.running import RunningCharger
from ankerprime.energy.recorder import EnergyRecorder
from ankerprime.energy.store import EnergyStore
from ankerprime.interfaces.channel import ChannelListener
from ankerprime.interfaces.storage import DocumentStore
from ankerprime.model.capabilities import Capability
from ankerprime.model.custom import ChargingProfile
from ankerprime.model.enums import (
    ChargingMode, DisplayLanguage, FixedAllocation,
    OutputId, ScreenOrientation, ScreenTimeout)
from ankerprime.model.errors import (
    AccountRefusedError, AnkerPrimeError, AuthenticationError,
    ChargerConnectionError, ChargerTimeoutError,
    InvalidParameterError, PictureTransferError, ProtocolError,
    UnsupportedCapabilityError, WriteRejectedError)
from ankerprime.model.pictures import JPEG_MAGIC, ScreenPictureUpload
from ankerprime.model.product import A2687
from ankerprime.model.state import DeviceInfo
from ankerprime.protocol.a2687 import commands, cover
from ankerprime.protocol.a2687.codes import (
    CHARGING_MODE_CODES, DISPLAY_LANGUAGE_CODES,
    FIELD_A1, FIXED_ALLOCATION_CODES, OP_BRIGHTNESS, OP_FIXED_ALLOCATION,
    OP_COVER_TRANSFER_CHUNK, OP_COVER_TRANSFER_START, OP_GYROSCOPE, OP_LANGUAGE,
    OP_ORIENTATION, OP_READ_ALL, OP_REALTIME_REPORT, OP_SCREEN_TIMEOUT,
    OP_SET_SCREENSAVER, OUTPUTS, SCREEN_ORIENTATION_CODES, SCREEN_TIMEOUT_CODES)
from ankerprime.protocol.a2687.commands import OutgoingMessage
from ankerprime.protocol.a2687.frame import Frame, decode_frame, encode_frame
from ankerprime.protocol.a2687.telemetry import decode_report
from ankerprime.protocol.a2687.tlv import TYPE_U8, Payload
from ankerprime.protocol.a2687.tracker import StateTracker
from ankerprime.protocol.codes import encode_value
from ankerprime.session.a2687 import A2687Session
from ankerprime.utils import Clock, copy_with, monotonic, redact, utc_now


logger = logging.getLogger(__name__)

Callback = Callable[[], None]

REPORT_OPCODES = frozenset({OP_REALTIME_REPORT, OP_READ_ALL})
DEFAULT_COUNTRY = 'US'


@dataclass(init=False)
class A2687Timings:
    """Timing rules, in seconds; the defaults follow Charker."""

    _poll_interval: float
    _stale_after: float
    _telemetry_deadline: float
    _opening_spacing: float
    _reply_timeout: float
    _retry_max: float
    _update_interval: float
    _linger: float
    _energy_save: float
    _tick: float
    _screen_picture: ScreenPictureTimings

    def __init__(self, *, poll_interval: float = 6.0,
                 stale_after: float = 20.0, telemetry_deadline: float = 20.0,
                 opening_spacing: float = 0.4, reply_timeout: float = 6.0,
                 retry_max: float = 30.0, update_interval: float = 60.0,
                 linger: float = 5.0, energy_save: float = 60.0,
                 tick: float = 1.0,
                 screen_picture: ScreenPictureTimings | None = None):
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._telemetry_deadline = telemetry_deadline
        self._opening_spacing = opening_spacing
        self._reply_timeout = reply_timeout
        self._retry_max = retry_max
        self._update_interval = update_interval
        self._linger = linger
        self._energy_save = energy_save
        self._tick = tick
        self._screen_picture = screen_picture or ScreenPictureTimings()

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @property
    def stale_after(self) -> float:
        return self._stale_after

    @property
    def telemetry_deadline(self) -> float:
        """Silence after which a kept connection is recycled."""

        return self._telemetry_deadline

    @property
    def opening_spacing(self) -> float:
        """Gap between the requests sent right after the negotiation."""

        return self._opening_spacing

    @property
    def reply_timeout(self) -> float:
        return self._reply_timeout

    @property
    def retry_max(self) -> float:
        return self._retry_max

    @property
    def update_interval(self) -> float:
        """Period of the reads without a kept connection."""

        return self._update_interval

    @property
    def linger(self) -> float:
        """How long an on-demand connection stays open after its last use."""

        return self._linger

    @property
    def energy_save(self) -> float:
        return self._energy_save

    @property
    def tick(self) -> float:
        return self._tick

    @property
    def screen_picture(self) -> ScreenPictureTimings:
        return self._screen_picture

    def retry_delay(self, failures: int) -> float:
        """Delay before reconnect attempt ``failures``: 2, 4, 8 ... s."""

        return min(float(1 << min(max(failures, 1), 6)), self._retry_max)

    def replace(self, **changes: Any) -> Self:
        """A copy with the given timings changed."""

        return copy_with(self, changes)

@dataclass(init=False)
class ScreenPictureTimings:
    """Pauses and limits of a screen picture transfer, from Charker.

    They are what the working client does; the firmware's real tolerances
    are unknown.
    """

    _settle_after_select: float
    _settle_after_start: float
    _chunk_spacing: float
    _acknowledgement_timeout: float
    _settle_before_verify: float
    _verify_attempts: int
    _verify_delay: float
    _budget: float

    def __init__(self, *, settle_after_select: float = 0.15,
                 settle_after_start: float = 0.2,
                 chunk_spacing: float = 0.04,
                 acknowledgement_timeout: float = 5.0,
                 settle_before_verify: float = 0.4,
                 verify_attempts: int = 3, verify_delay: float = 1.0,
                 budget: float = 240.0):
        self._settle_after_select = settle_after_select
        self._settle_after_start = settle_after_start
        self._chunk_spacing = chunk_spacing
        self._acknowledgement_timeout = acknowledgement_timeout
        self._settle_before_verify = settle_before_verify
        self._verify_attempts = verify_attempts
        self._verify_delay = verify_delay
        self._budget = budget

    @property
    def settle_after_select(self) -> float:
        return self._settle_after_select

    @property
    def settle_after_start(self) -> float:
        return self._settle_after_start

    @property
    def chunk_spacing(self) -> float:
        """Pause after each slice the firmware does not answer."""

        return self._chunk_spacing

    @property
    def acknowledgement_timeout(self) -> float:
        return self._acknowledgement_timeout

    @property
    def settle_before_verify(self) -> float:
        """``E1`` does not change the moment the last slice lands."""

        return self._settle_before_verify

    @property
    def verify_attempts(self) -> int:
        return self._verify_attempts

    @property
    def verify_delay(self) -> float:
        return self._verify_delay

    @property
    def budget(self) -> float:
        """Ceiling on sending the slices."""

        return self._budget

    def replace(self, **changes: Any) -> Self:
        """A copy with the given timings changed."""

        return copy_with(self, changes)

class _Waiter:
    """A request waiting for a frame with one of the opcodes."""

    _opcodes: frozenset[int]
    _future: asyncio.Future[Payload]

    def __init__(self, opcodes: Iterable[int],
                 future: asyncio.Future[Payload]):
        self._opcodes = frozenset(opcodes)
        self._future = future

    @property
    def opcodes(self) -> frozenset[int]:
        return self._opcodes

    @property
    def future(self) -> asyncio.Future[Payload]:
        return self._future


class A2687Device(RunningCharger, ChannelListener):
    """One A2687 behind an encrypted Bluetooth session."""

    _session: A2687Session
    _base_info: DeviceInfo
    _info: DeviceInfo
    _keep_connection: bool
    _country: str
    _owner_id: str | None
    _timings: A2687Timings

    _tracker: StateTracker
    _available: bool
    _energy_documents: DocumentStore | None

    _supervisor: asyncio.Task[None] | None
    _linger_task: asyncio.Task[None] | None
    _reader: asyncio.Task[None] | None
    _wake: asyncio.Event

    _stopping: bool
    _link_open: bool
    _link_lost: bool
    _link_lock: asyncio.Lock
    _link_users: int
    _write_lock: asyncio.Lock
    _waiters: list[_Waiter]
    _transferring: bool

    _failures: int
    _next_attempt: float
    _next_read: float
    _opened_at: float
    _last_poll: float
    _poll_count: int
    _last_telemetry: float | None

    def __init__(self, session: A2687Session, info: DeviceInfo | None = None,
                 *, keep_connection: bool = True,
                 country: str = DEFAULT_COUNTRY,
                 owner_id: str | None = None,
                 energy_store: DocumentStore | None = None,
                 timings: A2687Timings | None = None,
                 clock: Clock | None = None):
        info = info or DeviceInfo()
        timings = timings or A2687Timings()
        energy = EnergyRecorder((spec.port for spec in A2687.ports),
                                label=redact(info.serial),
                                save_interval=timings.energy_save)
        super().__init__(A2687, energy, clock or utc_now)
        self._session = session
        self._base_info = info
        self._info = info
        self._keep_connection = keep_connection
        self._country = country
        self._owner_id = owner_id or None
        self._timings = timings
        self._tracker = StateTracker(A2687)
        self._available = False
        self._energy_documents = energy_store
        self._attach_energy_store(info.serial)
        self._supervisor = None
        self._linger_task = None
        self._reader = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._link_open = False
        self._link_lost = False
        self._link_lock = asyncio.Lock()
        self._link_users = 0
        self._write_lock = asyncio.Lock()
        self._waiters = []
        self._transferring = False
        self._failures = 0
        self._next_attempt = 0.0
        self._next_read = 0.0
        self._opened_at = 0.0
        self._last_poll = 0.0
        self._poll_count = 0
        self._last_telemetry = None
        self._publish_state()

    @property
    def connected(self) -> bool:
        return self._link_open and self._session.is_open

    @property
    def keep_connection(self) -> bool:
        return self._keep_connection

    def on_message(self, data: bytes) -> None:
        try:
            frame = decode_frame(data)
            payload = Payload.parse(frame.payload)
        except ProtocolError as error:
            logger.debug('ignored frame from %s: %s', self._label, error)
            return
        report = decode_report(payload)
        if self._tracker.apply(report, self._clock()):
            if report.ports:
                now = monotonic()
                self._last_telemetry = now
                self._failures = 0
                powers = {port: reading.power
                          for port, reading in report.ports.items()}
                self._energy.record(now, powers)
                self._available = True
            self._publish_state()
        for waiter in self._waiters:
            if frame.opcode in waiter.opcodes and not waiter.future.done():
                waiter.future.set_result(payload)

    def on_closed(self, error: Exception | None) -> None:
        if error is not None:
            logger.debug('link to %s lost: %s', self._label, error)
        self._link_lost = True
        self._fail_waiters(ChargerConnectionError('link to charger lost'))
        self._wake.set()

    async def connect(self) -> None:
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._stopping = False
        self._last_error = None
        self._failures = 0
        if await self._energy.load():
            self._publish_state()
        if self._keep_connection:
            await self._open_link()
        else:
            await self._read_state()
        self._next_read = monotonic() + self._timings.update_interval
        self._supervisor = asyncio.create_task(
            self._supervise(), name='ankerprime-a2687-supervisor')

    async def disconnect(self) -> None:
        self._stopping = True
        await self._cancel_tasks(self._supervisor, self._linger_task)
        self._supervisor = None
        self._linger_task = None
        self._reader = None
        await self._close_link()
        self._energy.interrupt()
        self._available = False
        self._publish_state()
        await self._save_energy()

    async def refresh(self) -> None:
        if self._keep_connection and not self.connected:
            raise ChargerConnectionError('charger is not connected')
        await self._read_state()

    async def _set_output(self, output: OutputId, on: bool) -> None:
        port = _port_index(output)
        await self._write(commands.set_port_output(port, on,
                                                   self._timestamp()))

    async def _start_countdown(self, output: OutputId, seconds: int) -> None:
        countdown = self._product.countdown
        if countdown is None or not countdown.contains(seconds):
            raise InvalidParameterError(f'countdown out of range: {seconds}')
        port = _port_index(output)
        await self._write(commands.set_port_timer(port, seconds,
                                                  self._timestamp()))

    async def _cancel_countdown(self, output: OutputId) -> None:
        port = _port_index(output)
        await self._write(commands.set_port_timer(port, 0, self._timestamp()))

    async def _set_charging_mode(self, mode: ChargingMode) -> None:
        if mode not in self._product.charging_modes:
            raise InvalidParameterError(f'charging mode not offered: {mode}')
        code = encode_value(CHARGING_MODE_CODES, mode)
        await self._write(commands.set_charging_mode(code, self._timestamp()),
                          lambda: self._tracker.apply_charging_mode(code))

    async def _set_fixed_allocation(self, allocation: FixedAllocation
                                    ) -> None:
        code = encode_value(FIXED_ALLOCATION_CODES, allocation)
        await self._write_setting(
            OP_FIXED_ALLOCATION, code,
            lambda: self._tracker.apply_fixed_allocation(allocation))

    async def _set_brightness(self, percent: int) -> None:
        brightness = self._product.brightness
        if brightness is None or not brightness.contains(percent):
            raise InvalidParameterError(f'brightness out of range: {percent}')
        await self._write_setting(OP_BRIGHTNESS, percent)

    async def _set_screen_timeout(self, timeout: ScreenTimeout) -> None:
        await self._write_setting(OP_SCREEN_TIMEOUT,
                                  encode_value(SCREEN_TIMEOUT_CODES, timeout))

    async def _set_screen_orientation(self, orientation: ScreenOrientation
                                      ) -> None:
        code = encode_value(SCREEN_ORIENTATION_CODES, orientation)
        await self._write_setting(OP_ORIENTATION, code)

    async def _set_auto_rotation(self, enabled: bool) -> None:
        await self._write_setting(OP_GYROSCOPE, int(enabled))

    async def _set_display_language(self, language: DisplayLanguage) -> None:
        code = encode_value(DISPLAY_LANGUAGE_CODES, language)
        await self._write_setting(
            OP_LANGUAGE, code, lambda: self._tracker.apply_language(language))

    async def _apply_custom_profile(self, profile: ChargingProfile) -> None:
        rules = self._product.custom_mode
        if rules is None:
            raise UnsupportedCapabilityError(Capability.CUSTOM_CHARGING_MODE)
        rules.validate(profile)
        watts = [profile.watts(output) for output in OUTPUTS]
        code = encode_value(CHARGING_MODE_CODES, ChargingMode.CUSTOM)
        message = commands.set_custom_charging_mode(
            profile.number, profile.auto_exit, watts, self._timestamp())
        # The read-back after the write shows whether the charger took it.
        await self._write(message,
                          lambda: self._tracker.apply_charging_mode(code))

    async def _exit_custom_mode(self) -> None:
        # There is no exit command; auto exit also returns to AI mode.
        await self._set_charging_mode(ChargingMode.AI)

    async def _upload_screen_picture(self, jpeg: bytes,
                                     acknowledge_irreversible: bool
                                     ) -> ScreenPictureUpload:
        if not acknowledge_irreversible:
            raise InvalidParameterError(
                'a screen picture cannot be removed from the charger; '
                'confirm with acknowledge_irreversible=True')
        if not jpeg.startswith(JPEG_MAGIC):
            raise InvalidParameterError('screen pictures must be JPEG files')
        async with self._write_lock, self._link():
            self._transferring = True
            try:
                return await self._transfer_picture(jpeg)
            finally:
                self._transferring = False

    async def _select_screen_picture(self, picture_id: int, crc32: int
                                     ) -> None:
        message = cover.select_cover(picture_id, crc32, self._timestamp())
        await self._write(message)

    async def _transfer_picture(self, jpeg: bytes) -> ScreenPictureUpload:
        """Select, announce, send the slices, then verify with ``E1``."""

        timings = self._timings.screen_picture
        before = await self._shown_picture()
        plan = cover.CoverPlan(jpeg, cover.mint_picture_id(jpeg, before))
        timestamp = self._timestamp()
        try:
            # A new picture is answered 0x11 ("pixels missing"): expected.
            await self._request(cover.select_cover(
                plan.picture_id, plan.crc32, timestamp), (OP_SET_SCREENSAVER,))
            await asyncio.sleep(timings.settle_after_select)
            reply = await self._request(cover.start_transfer(plan, timestamp),
                                        (OP_COVER_TRANSFER_START,))
        except ChargerTimeoutError as error:
            raise PictureTransferError(f'screen picture not started: {error}'
                                       ) from None
        if not reply.ok:
            raise PictureTransferError(
                f'charger refused the screen picture (status '
                f'{reply.status:#04x})')
        await asyncio.sleep(timings.settle_after_start)
        await self._send_slices(plan, timings)
        await asyncio.sleep(timings.settle_before_verify)
        shown = None
        for attempt in range(max(1, timings.verify_attempts)):
            if attempt:
                await asyncio.sleep(timings.verify_delay)
            shown = await self._shown_picture()
            if shown == plan.reported_id:
                break
        if shown != plan.reported_id:
            raise PictureTransferError(
                'the picture reached the charger but the screen does not '
                'show it; try select_screen_picture', pixels_written=True)
        return ScreenPictureUpload(
            plan.picture_id, plan.crc32, byte_count=plan.byte_count,
            chunk_count=plan.chunk_count, previous=before,
            witnessed=before is not None and before != plan.reported_id)

    async def _send_slices(self, plan: cover.CoverPlan,
                           timings: ScreenPictureTimings) -> None:
        deadline = monotonic() + timings.budget
        total = plan.chunk_count
        for index, payload in plan.chunks():
            written = index > 0
            if monotonic() >= deadline:
                raise PictureTransferError(
                    f'screen picture too slow: {index} of {total} slices '
                    'sent', pixels_written=written)
            message = cover.transfer_chunk(index, payload)
            try:
                if not plan.is_checkpoint(index):
                    await self._send(message)
                    await asyncio.sleep(timings.chunk_spacing)
                    continue
                reply = await self._request(
                    message, (OP_COVER_TRANSFER_CHUNK,),
                    timeout=timings.acknowledgement_timeout)
            except (ChargerConnectionError, ChargerTimeoutError) as error:
                raise PictureTransferError(
                    f'screen picture stopped at slice {index} of {total}: '
                    f'{error}', pixels_written=True) from None
            final = index == total - 1
            # The last slice is answered 0x10 once the picture is stored.
            done = final and reply.status == cover.TRANSFER_COMPLETE
            if not reply.ok and not done:
                raise PictureTransferError(
                    f'charger refused slice {index} (status '
                    f'{reply.status:#04x})', pixels_written=True)
            expected = cover.next_index(reply)
            if expected is not None and expected != index + 1:
                raise PictureTransferError(
                    f'charger expects slice {expected} after {index}; '
                    'slices were lost', pixels_written=True)

    async def _shown_picture(self) -> int | None:
        """``E1`` from a fresh read-all; None when it cannot be read."""

        try:
            payload = await self._request(
                commands.read_all(self._timestamp()), REPORT_OPCODES)
        except ChargerTimeoutError:
            return None
        settings = decode_report(payload).settings
        return settings.screen_picture if settings is not None else None

    async def _supervise(self) -> None:
        """Keep the link (or the periodic reads), staleness and energy."""

        timings = self._timings
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), timings.tick)
            except TimeoutError:
                pass
            self._wake.clear()
            now = monotonic()
            try:
                if self._link_lost:
                    await self._close_link()
                if self._keep_connection:
                    await self._keep_link(now)
                elif (now >= self._next_read and not self._transferring
                      and (self._reader is None or self._reader.done())):
                    self._next_read = now + timings.update_interval
                    self._reader = self._spawn(self._read_state())
            except AuthenticationError as error:
                logger.warning('%s refused the account; give the owner '
                               'account ID', self._label)
                self._last_error = error
                await self._close_link()
                self._update_availability(now)
                self._publish_state()
                return
            except Exception:
                logger.exception('unexpected error supervising %s',
                                 self._label)
            self._update_availability(now)
            self._save_energy_if_due(now)

    async def _keep_link(self, now: float) -> None:
        """Reopen a lost link, recycle a silent one and poll."""

        timings = self._timings
        if not self._link_open:
            if now < self._next_attempt:
                return
            try:
                await self._open_link()
            except AuthenticationError:
                raise
            except Exception as error:
                self._failures += 1
                self._next_attempt = now + timings.retry_delay(self._failures)
                if isinstance(error, AnkerPrimeError):
                    logger.debug('reconnecting %s failed: %s', self._label,
                                 error)
                else:
                    logger.exception('unexpected error reconnecting %s',
                                     self._label)
            return
        if self._transferring:
            # Nothing may interleave with the slices of a screen picture.
            return
        reference = self._last_telemetry or self._opened_at
        if now - reference >= timings.telemetry_deadline:
            self._raise_if_refused()
            logger.debug('no reports from %s; recycling the link',
                         self._label)
            await self._close_link()
            self._failures += 1
            self._next_attempt = now + timings.retry_delay(self._failures)
            return
        if now - self._last_poll >= timings.poll_interval:
            self._last_poll = now
            self._poll_count += 1
            timestamp = self._timestamp()
            try:
                await self._send(commands.realtime_probe(
                    self._country, self._owner_id, timestamp))
                if self._poll_count % 2 == 0:
                    await self._send(commands.read_all(timestamp))
            except ChargerConnectionError:
                pass

    async def _open_link(self) -> None:
        """Negotiate a session and send the opening requests."""

        self._session.set_listener(self)
        self._link_lost = False
        await self._session.open()
        self._link_open = True
        now = monotonic()
        self._opened_at = now
        self._last_poll = now
        self._poll_count = 0
        self._last_telemetry = None
        self._info = _merge_info(self._base_info, self._session.device_info)
        if self._attach_energy_store(self._info.serial):
            await self._energy.load()
        self._publish_state()
        spacing = self._timings.opening_spacing
        timestamp = self._timestamp()
        try:
            await self._send(commands.read_all(timestamp))
            await asyncio.sleep(spacing)
            await self._send(commands.realtime_probe(
                self._country, self._owner_id, timestamp))
            await asyncio.sleep(spacing)
            await self._send(commands.realtime_trigger(timestamp))
        except ChargerConnectionError:
            await self._close_link()
            raise

    async def _close_link(self) -> None:
        linger = self._linger_task
        if linger is not None and linger is not asyncio.current_task():
            linger.cancel()
            self._linger_task = None
        was_open = self._link_open
        self._link_open = False
        self._link_lost = False
        self._fail_waiters(ChargerConnectionError('link to charger closed'))
        await self._session.close()
        if was_open and self._keep_connection:
            self._available = False
            self._publish_state()

    @asynccontextmanager
    async def _link(self) -> AsyncIterator[None]:
        """Use the link; without a kept connection open it and linger after."""

        if self._keep_connection:
            if not self.connected:
                raise ChargerConnectionError('charger is not connected')
            yield
            return
        async with self._link_lock:
            self._link_users += 1
            if self._linger_task is not None:
                self._linger_task.cancel()
                self._linger_task = None
            if not self.connected:
                try:
                    await self._open_link()
                except BaseException:
                    self._link_users -= 1
                    raise
        try:
            yield
        finally:
            self._link_users -= 1
            if self._link_users == 0 and not self._stopping:
                self._linger_task = asyncio.create_task(self._linger())

    async def _linger(self) -> None:
        await asyncio.sleep(self._timings.linger)
        async with self._link_lock:
            if self._link_users == 0:
                self._linger_task = None
                await self._close_link()

    async def _read_state(self) -> None:
        """Read everything and wait for the report."""

        async with self._link():
            try:
                await self._request(commands.read_all(self._timestamp()),
                                    REPORT_OPCODES)
            except ChargerTimeoutError:
                self._raise_if_refused()
                raise

    async def _write_setting(self, opcode: int, value: int,
                             on_success: Callback | None = None) -> None:
        await self._write(commands.set_setting(opcode, value,
                                               self._timestamp()),
                          on_success)

    async def _write(self, message: OutgoingMessage,
                     on_success: Callback | None = None) -> None:
        """Send a write, check its reply, then read everything back."""

        async with self._write_lock, self._link():
            reply = await self._request(message, (message.opcode,))
            status = _refusal(reply)
            if status is not None:
                raise WriteRejectedError(status)
            if on_success is not None:
                on_success()
                self._publish_state()
            try:
                await self._request(commands.read_all(self._timestamp()),
                                    REPORT_OPCODES)
            except ChargerTimeoutError:
                logger.debug('no read-back from %s after a write',
                             self._label)

    async def _request(self, message: OutgoingMessage,
                       opcodes: Iterable[int], *,
                       timeout: float | None = None) -> Payload:
        future = asyncio.get_running_loop().create_future()
        waiter = _Waiter(opcodes, future)
        self._waiters.append(waiter)
        if timeout is None:
            timeout = self._timings.reply_timeout
        try:
            await self._send(message)
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            raise ChargerTimeoutError(
                f'charger did not answer {message.opcode:#06x}') from None
        finally:
            self._waiters.remove(waiter)

    async def _send(self, message: OutgoingMessage) -> None:
        frame = Frame.build(message.group, message.opcode, message.plaintext,
                            encrypted=False)
        try:
            await self._session.send(encode_frame(frame))
        except ChargerConnectionError:
            self._link_lost = True
            self._wake.set()
            raise

    def _raise_if_refused(self) -> None:
        if self._session.auth_rejected and self._last_telemetry is None:
            raise AccountRefusedError('charger refused the user identity '
                                      'and sends no data; set the owner '
                                      'account ID')

    def _update_availability(self, now: float) -> None:
        window = self._timings.stale_after
        if not self._keep_connection:
            window += self._timings.update_interval
        fresh = (self._last_telemetry is not None
                 and now - self._last_telemetry <= window
                 and (self._link_open or not self._keep_connection))
        if fresh != self._available:
            self._available = fresh
            self._publish_state()

    def _attach_energy_store(self, serial: str | None) -> bool:
        """Key the energy counters by serial once known; True if attached."""

        if (serial is None or self._energy_documents is None
                or self._energy.has_store):
            return False
        self._energy.attach(EnergyStore.for_serial(self._energy_documents,
                                                   serial))
        return True

    def _fail_waiters(self, error: ChargerConnectionError) -> None:
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.set_exception(error)

    def _task_failed(self, error: BaseException) -> bool:
        if isinstance(error, AuthenticationError):
            self._last_error = error
            self._stopping = True
            self._publish_state()
            return True
        if isinstance(error, AnkerPrimeError):
            logger.debug('request to %s failed: %s', self._label, error)
            return True
        return False

    @property
    def _label(self) -> str:
        return redact(self._info.serial or self._info.mac_address)

    def _publish_state(self) -> None:
        state = self._tracker.build(info=self._info,
                                    available=self._available,
                                    energy=self._energy.counters)
        self._publish(state)


def _port_index(output: OutputId) -> int:
    if output not in OUTPUTS:
        raise InvalidParameterError(f'{A2687.code} has no output {output}')
    return OUTPUTS.index(output)


def _refusal(reply: Payload) -> int | None:
    """Return the status of a refused write, None when it was accepted.

    A refusal is a non-zero leading status byte, or a non-zero typed u8
    result in A1 (port output replies).
    """

    if not reply.ok:
        return reply.status
    result = reply.typed(FIELD_A1)
    if result is not None and result.type_code == TYPE_U8:
        code = result.scalar
        if code:
            return code
    return None


def _merge_info(base: DeviceInfo, negotiated: DeviceInfo) -> DeviceInfo:
    """The caller's identity, completed by the negotiation's base info."""

    return DeviceInfo(serial=negotiated.serial or base.serial,
                      name=base.name,
                      firmware_version=(negotiated.firmware_version
                                        or base.firmware_version),
                      hardware_version=base.hardware_version,
                      mac_address=negotiated.mac_address or base.mac_address)

