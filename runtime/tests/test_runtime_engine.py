"""The scheduler: urgent work overtakes background work within one step."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from nexus_runtime.engine import Priority, Scheduler


class StepJob:
    """A job of ``steps`` steps that records each one in a shared log."""

    def __init__(self, name: str, priority: Priority, steps: int, log: list, delay: float = 0.002):
        self.name, self.priority, self.left, self.log, self.delay = name, priority, steps, log, delay
        self.cancelled = False
        self.done = threading.Event()

    def step(self) -> bool:
        time.sleep(self.delay)
        self.log.append(self.name)
        self.left -= 1
        if self.left == 0:
            self.done.set()
            return True
        return False

    def fail(self, exc: BaseException) -> None:
        self.done.set()


@pytest.fixture
def scheduler():
    s = Scheduler()
    s.start()
    yield s
    s.stop()


def test_live_work_overtakes_bulk_work_mid_flight(scheduler):
    log: list[str] = []
    bulk = StepJob("bulk", Priority.BULK, steps=50, log=log)
    scheduler.submit(bulk)
    while "bulk" not in log:  # bulk has started
        time.sleep(0.001)
    live = StepJob("live", Priority.LIVE, steps=5, log=log)
    scheduler.submit(live)
    assert live.done.wait(5) and bulk.done.wait(5)

    first_live = log.index("live")
    last_live = len(log) - 1 - log[::-1].index("live")
    # Once live arrived, it ran without bulk interleaving, then bulk resumed.
    assert log[first_live : last_live + 1] == ["live"] * 5
    assert "bulk" in log[last_live + 1 :]
    assert log.count("bulk") == 50


def test_equal_priority_runs_in_arrival_order(scheduler):
    log: list[str] = []
    gate = StepJob("gate", Priority.INTERACTIVE, steps=3, log=log, delay=0.01)
    title = StepJob("title", Priority.INTERACTIVE, steps=3, log=log)
    scheduler.submit(gate)
    scheduler.submit(title)
    assert gate.done.wait(5) and title.done.wait(5)
    assert log == ["gate"] * 3 + ["title"] * 3


def test_a_cancelled_job_takes_no_more_steps(scheduler):
    log: list[str] = []
    job = StepJob("doomed", Priority.BULK, steps=1000, log=log, delay=0.005)
    scheduler.submit(job)
    while not log:
        time.sleep(0.001)
    job.cancelled = True
    time.sleep(0.1)
    steps = len(log)
    time.sleep(0.1)
    assert len(log) == steps < 1000


async def test_call_returns_the_result_and_raises_the_error(scheduler):
    assert await scheduler.call(lambda: 41 + 1) == 42

    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await scheduler.call(boom)


async def test_queue_depth_reports_waiting_work(scheduler):
    blocker = threading.Event()
    scheduler.submit(type("Block", (), {
        "priority": Priority.LIVE, "cancelled": False,
        "step": lambda self: blocker.wait(5) or True, "fail": lambda self, e: None,
    })())
    log: list[str] = []
    scheduler.submit(StepJob("bulk", Priority.BULK, steps=1, log=log))
    await asyncio.sleep(0.05)
    depth = scheduler.queue_depth()
    blocker.set()
    assert depth["bulk"] == 1


def test_priority_header_parsing():
    assert Priority.parse("live") is Priority.LIVE
    assert Priority.parse("BULK") is Priority.BULK
    assert Priority.parse(None) is Priority.INTERACTIVE
    assert Priority.parse("whatever") is Priority.INTERACTIVE


def test_a_dropped_job_is_closed_on_the_worker_thread(scheduler):
    """MLX state must be released on the thread that made it."""
    closed_on: list[str] = []
    log: list[str] = []
    job = StepJob("doomed", Priority.BULK, steps=1000, log=log, delay=0.005)
    job.close = lambda: closed_on.append(threading.current_thread().name)
    scheduler.submit(job)
    while not log:
        time.sleep(0.001)
    job.cancelled = True
    for _ in range(200):
        if closed_on:
            break
        time.sleep(0.005)
    assert closed_on == ["mlx-worker"]
