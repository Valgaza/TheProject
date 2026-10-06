"""One worker thread that owns the GPU, and the order it does work in.

Every model call in Nexus lands here: the answer a person is waiting for, the
write gate deciding what to remember, graphiti extracting a graph in the
background. On one GPU they cannot all run at once, and the order matters more
than raw throughput -- a 90-second extraction must never sit in front of a
reply someone is watching.

So work is split into steps. A generation is one step per token; anything else
(an embedding batch, loading a model) is a single step. After every step the
worker picks the highest-priority job that is ready, which means a live reply
overtakes background work within one token of arriving, and background work
resumes where it left off once nothing live is waiting.

The thread is also an MLX requirement in practice: arrays and streams belong
to the thread that made them, so all model work happens on this one.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Lower runs first."""

    LIVE = 0          # a reply someone is waiting on; embeddings
    INTERACTIVE = 1   # titles, the write gate, the web-query rewrite
    BULK = 2          # graph extraction

    @classmethod
    def parse(cls, value: str | None) -> "Priority":
        try:
            return cls[(value or "interactive").upper()]
        except KeyError:
            return cls.INTERACTIVE


class Job(Protocol):
    priority: Priority
    cancelled: bool

    def step(self) -> bool:
        """Do one unit of work. Return True when the job is finished."""

    def fail(self, exc: BaseException) -> None:
        """Report an exception raised by step() to whoever is waiting."""


@dataclass
class CallJob:
    """A single step: run ``fn`` and hand its result to an asyncio future."""

    fn: Callable[[], Any]
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    priority: Priority = Priority.LIVE
    cancelled: bool = False

    def step(self) -> bool:
        result = self.fn()
        self.loop.call_soon_threadsafe(_resolve, self.future, result)
        return True

    def fail(self, exc: BaseException) -> None:
        self.loop.call_soon_threadsafe(_reject, self.future, exc)


def _resolve(future: asyncio.Future, result: Any) -> None:
    if not future.done():
        future.set_result(result)


def _reject(future: asyncio.Future, exc: BaseException) -> None:
    if not future.done():
        future.set_exception(exc)


@dataclass(order=True)
class _Entry:
    priority: int
    seq: int
    job: Any = field(compare=False)


class Scheduler:
    def __init__(self) -> None:
        self._jobs: list[_Entry] = []
        self._cond = threading.Condition()
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.busy_with: Priority | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mlx-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=10)

    # ── submitting work ──────────────────────────────────────────────────────

    def submit(self, job: Job) -> None:
        with self._cond:
            self._jobs.append(_Entry(int(job.priority), next(self._seq), job))
            self._cond.notify()

    async def call(self, fn: Callable[[], Any], priority: Priority = Priority.LIVE) -> Any:
        """Run ``fn`` on the worker thread and await its result."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.submit(CallJob(fn=fn, loop=loop, future=future, priority=priority))
        return await future

    def queue_depth(self) -> dict[str, int]:
        with self._cond:
            depth = {p.name.lower(): 0 for p in Priority}
            for entry in self._jobs:
                depth[Priority(entry.priority).name.lower()] += 1
            return depth

    # ── the worker ───────────────────────────────────────────────────────────

    def _next(self) -> _Entry | None:
        with self._cond:
            while True:
                while not self._jobs and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return None
                # Smallest (priority, seq): the most urgent, and within a
                # priority the oldest, so equal jobs run in arrival order.
                entry = min(self._jobs)
                if entry.job.cancelled:
                    # The caller went away (a closed stream, a barge-in);
                    # drop it without spending another token on it. Closed
                    # here because this is the worker thread, where MLX
                    # state has to be released.
                    self._jobs.remove(entry)
                    close = getattr(entry.job, "close", None)
                    if close is not None:
                        try:
                            close()
                        except Exception:
                            logger.exception("closing a cancelled job failed")
                    continue
                return entry

    def _run(self) -> None:
        while True:
            entry = self._next()
            if entry is None:
                return
            job = entry.job
            self.busy_with = Priority(entry.priority)
            try:
                finished = job.step()
            except BaseException as exc:  # noqa: BLE001 -- reported to the caller
                logger.exception("job failed priority=%s", Priority(entry.priority).name)
                finished = True
                try:
                    job.fail(exc)
                except Exception:
                    logger.exception("could not report a job failure")
            finally:
                self.busy_with = None
            if finished:
                with self._cond:
                    if entry in self._jobs:
                        self._jobs.remove(entry)
