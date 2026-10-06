"""The memory write path as a durable queue, kept out of the way of replies.

A finished turn is stored with a ``gate_decisions`` row in 'pending'
(memory_writer.enqueue_turn). One worker here drains those rows, oldest
first, through the write gate and into the graph.

Two properties the FastAPI background tasks it replaces did not have:

- **Durable.** The queue is a table. Core restarting mid-write loses nothing:
  rows left 'running' go back to 'pending', and episodes left pending with
  their text are re-ingested.
- **Out of the way.** The worker only starts a new turn's gate while nothing
  live is happening -- no reply being generated, no voice session open.
  The runtime already puts live work first on the GPU; this stops the gate
  and graphiti from even queueing up behind a person.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from .memory_client import MemoryClient
from .memory_writer import process_decision, resume_episode
from .providers.base import Provider

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class Activity:
    """Whether anything live is happening right now.

    Live work registers for its duration with ``async with activity.live():``
    -- a reply being generated, an open voice session. The write queue waits
    for none to be running before it starts its next job.
    """

    def __init__(self) -> None:
        self._live = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def busy(self) -> bool:
        return self._live > 0

    @asynccontextmanager
    async def live(self):
        self._live += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._live -= 1
            if self._live == 0:
                self._idle.set()

    async def wait_idle(self, grace: float) -> None:
        """Return once nothing has been live for ``grace`` seconds.

        The grace period stops the queue slipping a job into the gap between
        two turns of the same conversation.
        """
        while True:
            await self._idle.wait()
            await asyncio.sleep(grace)
            if not self.busy:
                return


class WriteQueue:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        provider: Provider,
        memory: MemoryClient,
        gate_model: str | None,
        activity: Activity,
        idle_grace: float = 1.5,
        poll_seconds: float = 5.0,
        decisions=None,
        laya_gate: str = "off",
        laya_skip_below: float = 0.15,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.memory = memory
        self.gate_model = gate_model
        self.activity = activity
        self.idle_grace = idle_grace
        self.poll_seconds = poll_seconds
        self.decisions = decisions
        self.laya_gate = laya_gate
        self.laya_skip_below = laya_skip_below
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def wake(self) -> None:
        """A turn was just queued; look now rather than at the next poll."""
        self._wake.set()

    def start(self) -> None:
        self._working = asyncio.Lock()
        self._task = asyncio.create_task(self._run(), name="write-queue")

    async def stop(self, grace: float = 30.0) -> None:
        """Stop between jobs: a turn being processed is finished first (up to
        ``grace`` seconds), rather than cut off inside its transaction.
        Recovery would redo it either way; this just avoids the redo."""
        if self._task:
            try:
                async with asyncio.timeout(grace):
                    await self._working.acquire()
            except TimeoutError:
                logger.warning("write queue: stopping mid-job after %.0fs", grace)
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _recover(self) -> None:
        """Pick up whatever a previous run left half done."""
        async with self.session_factory() as session:
            reset = await session.execute(text(
                "update gate_decisions set status = 'pending', updated_at = now() "
                "where status = 'running' returning id"
            ))
            stranded = await session.execute(text(
                "select id from memory_episodes where status = 'pending' and body is not null "
                "order by created_at"
            ))
            await session.commit()
            resumed, episodes = len(reset.all()), [row.id for row in stranded]
        if resumed or episodes:
            logger.info("write queue recovering: %d turns back to pending, %d episodes to retry",
                        resumed, len(episodes))
        for episode_id in episodes:
            await self.activity.wait_idle(self.idle_grace)
            await resume_episode(
                session_factory=self.session_factory, memory=self.memory, episode_id=episode_id
            )

    async def _claim(self):
        """Take the oldest pending turn, atomically, or None."""
        async with self.session_factory() as session:
            row = (await session.execute(text(
                "update gate_decisions set status = 'running', attempts = attempts + 1, "
                "updated_at = now() where id = ("
                "  select id from gate_decisions where status = 'pending' "
                "  order by created_at limit 1 for update skip locked"
                ") returning id, attempts"
            ))).first()
            await session.commit()
            return row

    async def _fail_or_retry(self, decision_id, attempts: int, exc: Exception) -> None:
        status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
        async with self.session_factory() as session:
            await session.execute(
                text("update gate_decisions set status = :s, error = :e, updated_at = :t where id = :i"),
                {"s": status, "e": f"{exc.__class__.__name__}: {exc}",
                 "t": datetime.now(timezone.utc), "i": decision_id},
            )
            await session.commit()

    async def drain_once(self) -> bool:
        """Process one queued turn if there is one. Returns whether it did."""
        row = await self._claim()
        if row is None:
            return False
        try:
            await process_decision(
                session_factory=self.session_factory, provider=self.provider,
                memory=self.memory, gate_model=self.gate_model, decision_id=row.id,
                decisions=self.decisions, laya_gate=self.laya_gate,
                laya_skip_below=self.laya_skip_below,
            )
        except Exception as exc:
            logger.exception("write queue: turn %s failed (attempt %d)", row.id, row.attempts)
            await self._fail_or_retry(row.id, row.attempts, exc)
        return True

    async def _run(self) -> None:
        try:
            await self._recover()
        except Exception:
            logger.exception("write queue recovery failed; continuing with new work")
        while True:
            try:
                await self.activity.wait_idle(self.idle_grace)
                async with self._working:
                    drained = await self.drain_once()
                if drained:
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                # The database blinking must not kill memory for the rest of
                # the process's life.
                logger.exception("write queue loop error; retrying shortly")
                await asyncio.sleep(self.poll_seconds)
