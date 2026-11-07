"""Runtime shared by the device implementations: energy and background tasks.

Every device counts energy with an ``EnergyRecorder``, saves it periodically
and on demand, and runs short background tasks it must be able to cancel on
``disconnect``. Only what a task error means differs per device
(``_task_failed``).
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from collections.abc import Collection, Coroutine
from typing import Any

from ankerprime.device.base import BaseCharger
from ankerprime.energy.recorder import EnergyRecorder
from ankerprime.model.enums import PortId
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.product import Product
from ankerprime.utils import Clock, monotonic


logger = logging.getLogger(__name__)


class RunningCharger(BaseCharger):
    """A connected charger: energy counters, clock and a task registry."""

    _clock: Clock
    _energy: EnergyRecorder
    _tasks: set[asyncio.Task[Any]]
    _saver: asyncio.Task[None] | None

    def __init__(self, product: Product, energy: EnergyRecorder,
                 clock: Clock):
        super().__init__(product)
        self._clock = clock
        self._energy = energy
        self._tasks = set()
        self._saver = None

    async def reset_energy(self, ports: Collection[PortId] | None = None
                           ) -> None:
        unknown = set(ports or ()) - set(self._energy.counters)
        if unknown:
            names = ', '.join(sorted(port.value for port in unknown))
            raise InvalidParameterError(
                f'{self._product.code} has no port {names}')
        self._energy.reset(ports, self._clock())
        self._publish_state()
        await self._save_energy()

    @property
    @abstractmethod
    def _label(self) -> str:
        """Redacted identity for log messages."""

        pass

    @abstractmethod
    def _publish_state(self) -> None:
        """Build the current state and publish it."""

        pass

    def _task_failed(self, error: BaseException) -> bool:
        """Handle an error of a background task; False logs it as unexpected."""

        return False

    async def _save_energy(self) -> None:
        if await self._energy.save(monotonic(), self._clock()):
            self._publish_state()

    def _save_energy_if_due(self, now: float) -> None:
        """Start a save when one is due and none is running."""

        if self._energy.save_due(now) and (self._saver is None
                                           or self._saver.done()):
            self._saver = self._spawn(self._save_energy())

    def _spawn(self, coroutine: Coroutine[Any, Any, None]
               ) -> asyncio.Task[None]:
        # The coroutine runs as the task itself: a task cancelled before its
        # first step then closes it cleanly, where a wrapper would leave it
        # never awaited.
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None or self._task_failed(error):
            return
        logger.error('unexpected error in a task of %s', self._label,
                     exc_info=error)

    async def _cancel_tasks(self, *others: asyncio.Task[Any] | None) -> None:
        """Cancel and await the registered tasks and the given ones."""

        tasks = [task for task in (*others, *self._tasks) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._saver = None

    def _timestamp(self) -> int:
        return int(self._clock().timestamp())
