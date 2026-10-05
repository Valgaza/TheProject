"""The durable write queue: turns wait in gate_decisions, one worker drains
them, nothing live waits on it, and a restart loses nothing."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select, text

from nexus_core.memory_client import IngestResult
from nexus_core.models import GateDecisionRecord, MemoryEpisode
from nexus_core.write_queue import Activity, WriteQueue

DEMO = uuid.UUID("00000000-0000-0000-0000-000000000001")


class ScriptedProvider:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, *, model=None, max_tokens=None, priority="interactive",
                   response_format=None):
        self.calls += 1
        return self.reply


class FakeMemory:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search(self, query, *, user_id, project_id=None, limit=None):
        return []

    async def ingest(self, statement, *, scope, user_id, project_id):
        self.calls.append({"statement": statement, "scope": scope, "project_id": project_id})
        return IngestResult(ok=True, group_id="g", episode_uuid=uuid.uuid4())


def _gate(*facts, persist=True, reason="kept"):
    return json.dumps({"persist": persist, "reason": reason,
                       "facts": [{"statement": s, "scope": sc} for s, sc in facts]})


async def _queued_turn(db_client, session_factory, content="I use Neo4j and Qdrant.") -> tuple:
    """A real stored turn, plus its queue row as the endpoint would write it."""
    db_client._transport.app.state.memory = FakeMemory()
    project = (await db_client.post("/projects", json={"name": "Q"})).json()
    chat = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()
    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": content})
    async with session_factory() as session:
        record = (await session.execute(
            select(GateDecisionRecord).where(GateDecisionRecord.chat_id == uuid.UUID(chat["id"]))
        )).scalars().one()
    return project["id"], chat["id"], record


def _queue(session_factory, provider, memory, activity=None) -> WriteQueue:
    return WriteQueue(
        session_factory=session_factory, provider=provider, memory=memory,
        gate_model="gate", activity=activity or Activity(), idle_grace=0.0, poll_seconds=0.05,
    )


async def test_a_finished_turn_is_queued_not_processed_inline(db_client, session_factory):
    _, _, record = await _queued_turn(db_client, session_factory)

    assert record.status == "pending"
    assert record.user_message == "I use Neo4j and Qdrant."
    assert record.assistant_message == "fake reply"
    assert record.persist is None


async def test_draining_decides_writes_and_records(db_client, session_factory):
    project_id, chat_id, record = await _queued_turn(db_client, session_factory)
    memory = FakeMemory()
    queue = _queue(session_factory, ScriptedProvider(_gate(
        ("Aarya uses Neo4j.", "project"), ("Aarya uses Qdrant.", "project"), ("Aarya is a student.", "user"),
    )), memory)

    assert await queue.drain_once() is True

    async with session_factory() as session:
        done = await session.get(GateDecisionRecord, record.id)
        episodes = (await session.execute(
            select(MemoryEpisode).where(MemoryEpisode.decision_id == record.id)
        )).scalars().all()
    assert done.status == "done" and done.persist is True and done.decided_by == "llm"
    assert len(done.facts) == 3
    # One episode per tier: the two project facts travel together.
    assert sorted(e.scope for e in episodes) == ["project", "user"]
    project_episode = next(e for e in episodes if e.scope == "project")
    assert project_episode.body == "Aarya uses Neo4j.\nAarya uses Qdrant."
    assert project_episode.status == "ingested"
    assert len(memory.calls) == 2


async def test_a_declined_turn_is_still_recorded(db_client, session_factory):
    _, _, record = await _queued_turn(db_client, session_factory, content="what's the time?")
    queue = _queue(session_factory, ScriptedProvider(_gate(persist=False, reason="a question")),
                   FakeMemory())

    await queue.drain_once()

    async with session_factory() as session:
        done = await session.get(GateDecisionRecord, record.id)
    assert (done.status, done.persist, done.reason) == ("done", False, "a question")


async def test_a_pleasantry_is_recorded_as_prefiltered(db_client, session_factory):
    _, _, record = await _queued_turn(db_client, session_factory, content="thanks!")
    provider = ScriptedProvider(_gate())
    await _queue(session_factory, provider, FakeMemory()).drain_once()

    async with session_factory() as session:
        done = await session.get(GateDecisionRecord, record.id)
    assert done.decided_by == "prefilter" and done.persist is False
    assert provider.calls == 0


async def test_an_empty_queue_drains_nothing(db_client, session_factory):
    queue = _queue(session_factory, ScriptedProvider(_gate()), FakeMemory())
    assert await queue.drain_once() is False


async def test_a_restart_puts_running_turns_back_and_retries_stranded_episodes(
    db_client, session_factory
):
    _, chat_id, record = await _queued_turn(db_client, session_factory)
    async with session_factory() as session:
        stored = await session.get(GateDecisionRecord, record.id)
        stored.status = "running"
        stranded = MemoryEpisode(
            chat_id=uuid.UUID(chat_id), decision_id=record.id, scope="user",
            status="pending", body="Aarya likes tea.",
        )
        session.add(stranded)
        await session.commit()
        stranded_id = stranded.id
    memory = FakeMemory()

    await _queue(session_factory, ScriptedProvider(_gate()), memory)._recover()

    async with session_factory() as session:
        assert (await session.get(GateDecisionRecord, record.id)).status == "pending"
        assert (await session.get(MemoryEpisode, stranded_id)).status == "ingested"
    assert memory.calls[0]["statement"] == "Aarya likes tea."


async def test_the_worker_waits_while_anything_is_live(db_client, session_factory):
    """Progress is watched through the fakes, and the database only read once
    the worker has stopped: the test's sessions share one connection, which
    cannot serve two concurrent users."""
    _, _, record = await _queued_turn(db_client, session_factory)
    activity = Activity()
    provider = ScriptedProvider(_gate(("Aarya uses Neo4j.", "user")))
    memory = FakeMemory()
    queue = _queue(session_factory, provider, memory, activity)

    async with activity.live():
        queue.start()
        await asyncio.sleep(0.3)
        assert provider.calls == 0  # a reply is "being generated": the gate waits
    for _ in range(100):
        if memory.calls:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    await queue.stop()

    assert provider.calls == 1 and len(memory.calls) == 1
    async with session_factory() as session:
        assert (await session.get(GateDecisionRecord, record.id)).status == "done"


async def test_a_failing_turn_is_retried_then_given_up(db_client, session_factory):
    _, _, record = await _queued_turn(db_client, session_factory)
    queue = _queue(session_factory, ScriptedProvider(_gate()), FakeMemory())

    async def boom(**kwargs):
        raise RuntimeError("db blinked")

    import nexus_core.write_queue as wq
    original = wq.process_decision
    wq.process_decision = boom
    try:
        for _ in range(wq.MAX_ATTEMPTS):
            await queue.drain_once()
    finally:
        wq.process_decision = original

    async with session_factory() as session:
        done = await session.get(GateDecisionRecord, record.id)
    assert done.status == "failed" and done.attempts == wq.MAX_ATTEMPTS
    assert "db blinked" in done.error
