"""What gets remembered, what gets dropped, and what happens when the gate misbehaves."""

from __future__ import annotations

import json
import uuid

import pytest

from sqlalchemy import select

from nexus_core.memory_client import IngestResult
from nexus_core.memory_writer import run_write_gate
from nexus_core.models import MemoryEpisode
from nexus_core.write_gate import GateDecision, decide, looks_trivial


class ScriptedProvider:
    """Returns whatever the gate is supposed to have said."""

    name = "scripted"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    async def chat(self, messages, *, model=None, max_tokens=None):
        self.calls.append(list(messages))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def embed(self, texts, *, model=None):
        return [[0.0] for _ in texts]


def _gate_json(**kwargs) -> str:
    return json.dumps({"persist": True, "scope": "user", "statement": "x", "reason": "r", **kwargs})


# ── The pre-filter ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["thanks", "Thanks!", "  ok  ", "hey", "got it", "cheers"])
def test_pleasantries_are_filtered_before_spending_a_call(text):
    assert looks_trivial(text)


@pytest.mark.parametrize(
    "text",
    [
        "I use Neo4j",  # eleven characters and it matters
        "thanks, and I'm switching to Postgres",
        "ok so the deadline is March",
        "no, we dropped that requirement",
    ],
)
def test_the_prefilter_does_not_eat_real_content(text):
    """Conservative on purpose: wrongly skipping a fact means a memory that
    silently never forms, which is worse than a wasted model call."""
    assert not looks_trivial(text)


async def test_a_pleasantry_never_reaches_the_model():
    provider = ScriptedProvider(_gate_json())

    decision = await decide(provider, user_message="thanks!", assistant_message="sure", subject_name="Aarya")

    assert decision.persist is False
    assert provider.calls == []


# ── Parsing ───────────────────────────────────────────────────────────────────


async def test_a_persist_decision_is_read_out():
    provider = ScriptedProvider(
        json.dumps(
            {
                "persist": True,
                "scope": "project",
                "statement": "The Nexus project uses Neo4j as its graph store.",
                "reason": "technology decision",
            }
        )
    )

    decision = await decide(
        provider, user_message="we went with Neo4j", assistant_message="noted"
    , subject_name="Aarya")

    assert decision.persist
    assert decision.scope == "project"
    assert decision.statement.startswith("The Nexus project uses Neo4j")


async def test_a_code_fence_around_the_json_is_tolerated():
    provider = ScriptedProvider("```json\n" + _gate_json() + "\n```")

    decision = await decide(provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya")

    assert decision.persist


async def test_unparseable_output_writes_nothing():
    """A gate that fails must fail closed. A missing memory is recoverable by
    saying the thing again; a wrong one has to be found first."""
    provider = ScriptedProvider("I think we should probably remember this!")

    decision = await decide(provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya")

    assert decision.persist is False
    assert "unparseable" in decision.reason


async def test_persist_without_a_valid_scope_is_refused():
    provider = ScriptedProvider(_gate_json(scope="everywhere"))

    decision = await decide(provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya")

    assert decision.persist is False


async def test_persist_without_a_statement_is_refused():
    provider = ScriptedProvider(_gate_json(statement="  "))

    decision = await decide(provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya")

    assert decision.persist is False


async def test_a_provider_failure_writes_nothing():
    provider = ScriptedProvider(RuntimeError("no credits"))

    decision = await decide(provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya")

    assert decision.persist is False
    assert "gate call failed" in decision.reason


async def test_the_gate_sees_both_halves_of_the_turn():
    provider = ScriptedProvider(_gate_json())

    await decide(
        provider, user_message="what did I pick?", assistant_message="you picked Neo4j"
    , subject_name="Aarya")

    turn = provider.calls[0][1]["content"]
    assert "what did I pick?" in turn
    assert "you picked Neo4j" in turn


# ── Provenance ────────────────────────────────────────────────────────────────


class FakeMemory:
    def __init__(self, result: IngestResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def ingest(self, statement, *, scope, user_id, project_id):
        self.calls.append(
            {"statement": statement, "scope": scope, "project_id": project_id}
        )
        return self.result


async def _seed_chat(db_client) -> tuple[str, str]:
    project = (await db_client.post("/projects", json={"name": "Gate"})).json()
    chat = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()
    return project["id"], chat["id"]


async def test_a_persisted_fact_gets_a_provenance_row(db_client, session_factory):
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()

    provider = ScriptedProvider(
        _gate_json(scope="project", statement="Nexus uses Neo4j.", reason="tech choice")
    )
    memory = FakeMemory(IngestResult(ok=True, group_id="p_x"))

    await run_write_gate(
        session_factory=session_factory,
        provider=provider,
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="we went with Neo4j",
        assistant_message="noted",
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat_id))
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].scope == "project"
    assert rows[0].status == "ingested"
    assert rows[0].reason == "tech choice"
    assert str(rows[0].chat_id) == chat_id


async def test_a_failed_ingest_is_recorded_as_failed(db_client, session_factory):
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(_gate_json()),
        memory=FakeMemory(IngestResult(ok=False, error="neo4j is down")),
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="a real fact here",
        assistant_message="ok",
    )

    async with session_factory() as session:
        row = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat_id))
            )
        ).scalars().one()

    assert row.status == "failed"
    assert "neo4j is down" in row.error


async def test_a_declined_turn_writes_no_row_at_all(db_client, session_factory):
    """Rejections are the common case by design. A table growing a row per
    "thanks" is a table nobody will read; the log line is the audit trail."""
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()
    memory = FakeMemory(IngestResult(ok=True))

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(json.dumps({"persist": False, "reason": "pleasantry"})),
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="how are you today",
        assistant_message="fine",
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat_id))
            )
        ).scalars().all()

    assert rows == []
    assert memory.calls == []


async def test_a_user_scope_fact_is_not_filed_under_the_project(db_client, session_factory):
    """The scope decision has to reach the write. Filing a durable personal
    fact into one project would hide it from every other one."""
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()
    memory = FakeMemory(IngestResult(ok=True))

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(
            _gate_json(scope="user", statement="Aarya prefers terse answers.")
        ),
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="keep answers short please",
        assistant_message="will do",
    )

    assert memory.calls[0]["scope"] == "user"
    assert memory.calls[0]["project_id"] is None


async def test_the_gate_is_told_who_it_is_writing_about():
    """Without a name it writes "The user prefers terse answers", and graphiti
    turns that into an entity literally called "The user"."""
    provider = ScriptedProvider(_gate_json())

    await decide(
        provider,
        user_message="keep answers short",
        assistant_message="will do",
        subject_name="Aarya",
    )

    turn = provider.calls[0][1]["content"]
    assert "Aarya" in turn
    assert "The person is named: Aarya" in turn
