"""What gets remembered, what gets dropped, and what happens when the gate misbehaves."""

from __future__ import annotations

import json
import uuid

import pytest

from sqlalchemy import select

from nexus_core.memory_client import IngestResult
from nexus_core.memory_writer import run_write_gate
from nexus_core.models import MemoryEpisode
from nexus_core.write_gate import (
    GateDecision,
    GateFact,
    _parse,
    decide,
    drop_known,
    looks_trivial,
    overlap,
)


class ScriptedProvider:
    """Returns whatever the gate is supposed to have said."""

    name = "scripted"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []
        self.response_formats: list[dict | None] = []

    async def chat(self, messages, *, model=None, max_tokens=None, priority="interactive",
                   response_format=None, temperature=None):
        self.calls.append(list(messages))
        self.response_formats.append(response_format)
        self.temperatures = getattr(self, "temperatures", []) + [temperature]
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def embed(self, texts, *, model=None):
        return [[0.0] for _ in texts]


def _gate_json(statement: str = "Aarya lives in Pune.", scope: str = "user", **kwargs) -> str:
    """One persist decision in the shape the gate now returns."""
    body = {
        "persist": True,
        "facts": [{"statement": statement, "scope": scope}],
        "reason": "r",
    }
    body.update(kwargs)
    return json.dumps(body)


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
        _gate_json("The Nexus project uses Neo4j as its graph store.", "project")
    )

    decision = await decide(
        provider, user_message="we went with Neo4j", assistant_message="noted"
    , subject_name="Aarya")

    assert decision.persist
    assert len(decision.facts) == 1
    assert decision.facts[0].scope == "project"
    assert decision.facts[0].statement.startswith("The Nexus project uses Neo4j")


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
    """It needs the reply to resolve references, and is told not to learn
    from it -- "yes, and it also needs retries" is a real fact whose content
    lives in the previous assistant message."""
    provider = ScriptedProvider(_gate_json())

    await decide(
        provider, user_message="what did I pick?", assistant_message="you picked Neo4j"
    , subject_name="Aarya")

    turn = provider.calls[0][1]["content"]
    assert "what did I pick?" in turn
    assert "you picked Neo4j" in turn
    assert "context only, not a source" in turn


# ── Provenance ────────────────────────────────────────────────────────────────


class FakeMemory:
    def __init__(self, result: IngestResult, known=()) -> None:
        self.result = result
        self.calls: list[dict] = []
        self.known = list(known)  # what a search finds
        self.retired: list[list[str]] = []

    async def search(self, query, *, user_id, project_id=None, limit=None):
        return self.known

    async def retire(self, facts, *, user_id, project_id):
        self.retired.append(list(facts))
        return [f"item-{n}" for n, _ in enumerate(facts)]

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
        _gate_json("Nexus uses Neo4j.", "project", reason="tech choice")
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
    """A declined turn reaches the graph in no form, so it leaves no episode
    row. (The decision itself is kept, in gate_decisions -- see
    test_write_queue.py -- because declines are the training data for a
    decision model, not because anything was written.)"""
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
            _gate_json("Aarya prefers terse answers.", "user")
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


async def test_project_scope_is_downgraded_to_user_for_an_unfiled_chat(db_client, session_factory):
    """The gate decides scope without knowing whether the chat even has a
    project (2026-09-03: chats can now be unfiled). Deciding "project" on
    one anyway must not write a memory_episodes row of scope "project"
    with no project_id -- there'd be nothing that could ever retrieve it
    by project again."""
    chat = (await db_client.post("/chats", json={})).json()
    turn = (
        await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello there"})
    ).json()
    memory = FakeMemory(IngestResult(ok=True))

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(
            _gate_json(scope="project", statement="This chat prefers Neo4j.")
        ),
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat["id"]),
        project_id=None,
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="we went with Neo4j",
        assistant_message="noted",
    )

    assert memory.calls[0]["scope"] == "user"
    assert memory.calls[0]["project_id"] is None

    async with session_factory() as session:
        row = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat["id"]))
            )
        ).scalars().one()
    assert row.scope == "user"


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


# ── Several facts per turn ────────────────────────────────────────────────────
# One message can carry a durable fact about the person and one about the work.
# The gate used to emit a single sentence, so one of the two was silently lost:
# on the 31 Aug run "my name is Aarya, I'm a final year B.Tech student, and I
# prefer short answers" wrote the identity and dropped the preference, while the
# gate's own reason said it had seen both.


def _multi_json(*pairs, reason: str = "r") -> str:
    return json.dumps(
        {
            "persist": True,
            "facts": [{"statement": s, "scope": sc} for s, sc in pairs],
            "reason": reason,
        }
    )


async def test_one_turn_can_carry_facts_for_two_different_tiers():
    provider = ScriptedProvider(
        _multi_json(
            ("Aarya is a final year B.Tech student.", "user"),
            ("Aarya prefers short, direct answers.", "user"),
            ("Aarya is building Nexus.", "project"),
        )
    )

    decision = await decide(
        provider,
        user_message="I'm a final year B.Tech student, I like short answers, and I'm building Nexus",
        assistant_message="noted",
        subject_name="Aarya",
    )

    assert [f.scope for f in decision.facts] == ["user", "user", "project"]
    assert any("prefers short" in f.statement for f in decision.facts)


async def test_more_facts_than_the_cap_are_trimmed():
    """Each fact costs 12-30s of ingest. Without a cap a long message becomes
    ten episodes restating one paragraph."""
    provider = ScriptedProvider(
        _multi_json(*[(f"Aarya knows fact {i}.", "user") for i in range(9)])
    )

    decision = await decide(
        provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya"
    )

    assert len(decision.facts) == 3


async def test_one_malformed_fact_does_not_lose_the_good_ones_beside_it():
    provider = ScriptedProvider(
        json.dumps(
            {
                "persist": True,
                "facts": [
                    {"statement": "Aarya uses Rust.", "scope": "project"},
                    {"statement": "", "scope": "user"},
                    {"statement": "Aarya is in Pune.", "scope": "nowhere"},
                ],
                "reason": "r",
            }
        )
    )

    decision = await decide(
        provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya"
    )

    assert [f.statement for f in decision.facts] == ["Aarya uses Rust."]


async def test_a_facts_list_that_is_entirely_junk_persists_nothing():
    provider = ScriptedProvider(json.dumps({"persist": True, "facts": [], "reason": "r"}))

    decision = await decide(
        provider, user_message="a real fact here", assistant_message="ok", subject_name="Aarya"
    )

    assert decision.persist is False


async def test_several_facts_write_several_provenance_rows(db_client, session_factory):
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()
    memory = FakeMemory(IngestResult(ok=True, group_id="g"))

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(
            _multi_json(
                ("Aarya is a final year B.Tech student.", "user"),
                ("Nexus uses Neo4j.", "project"),
            )
        ),
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="I'm a final year student and Nexus uses Neo4j",
        assistant_message="noted",
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat_id))
            )
        ).scalars().all()

    assert sorted(r.scope for r in rows) == ["project", "user"]
    # Each lands in its own tier, and only the project one carries the project.
    by_scope = {c["scope"]: c for c in memory.calls}
    assert by_scope["user"]["project_id"] is None
    assert by_scope["project"]["project_id"] == uuid.UUID(project_id)


# ── Not re-learning what was just recalled ────────────────────────────────────
# The loop this closes: asking "what am I using for the graph store?" made the
# assistant answer "Neo4j", and the gate wrote that answer back as a new fact.
# Seven episodes for four distinct facts, and one node summary holding the same
# sentence five times.


def test_overlap_scores_a_repeat_high_and_a_new_fact_low():
    same = overlap(
        "Aarya uses Neo4j as the graph store.",
        "Aarya uses Neo4j as the graph store.",
    )
    rephrased = overlap(
        "Aarya's current graph store for the project is Neo4j.",
        "Aarya is using Neo4j as the graph store for this project.",
    )
    unrelated = overlap(
        "Aarya is a final year B.Tech student.",
        "Aarya is building Nexus, a local AI orchestration system.",
    )

    assert same == 1.0
    assert rephrased > unrelated
    assert unrelated < 0.5


def test_drop_known_removes_a_restated_fact_and_keeps_a_new_one():
    facts = (
        GateFact("Aarya uses Neo4j as the graph store.", "project"),
        GateFact("Aarya's deadline is in March.", "project"),
    )

    kept, dropped = drop_known(facts, ["Aarya uses Neo4j as the graph store."])

    assert [f.statement for f in kept] == ["Aarya's deadline is in March."]
    assert len(dropped) == 1


async def test_recalled_facts_are_named_in_the_prompt():
    provider = ScriptedProvider(_gate_json())

    await decide(
        provider,
        user_message="what am I using for the graph store?",
        assistant_message="Neo4j",
        subject_name="Aarya",
        known_facts=["Aarya uses Neo4j as the graph store."],
    )

    prompt = provider.calls[0][1]["content"]
    assert "Already in memory, numbered; do not restate" in prompt
    assert "[1] Aarya uses Neo4j as the graph store." in prompt


async def test_restating_a_recalled_fact_persists_nothing():
    """The backstop behind the prompt, for when the gate ignores it."""
    provider = ScriptedProvider(
        _gate_json("Aarya uses Neo4j as the graph store.", "project")
    )

    decision = await decide(
        provider,
        user_message="what am I using for the graph store?",
        assistant_message="You're using Neo4j.",
        subject_name="Aarya",
        known_facts=["Aarya uses Neo4j as the graph store."],
    )

    assert decision.persist is False
    assert "already in memory" in decision.reason


async def test_a_genuinely_new_fact_survives_alongside_a_restated_one():
    provider = ScriptedProvider(
        _multi_json(
            ("Aarya uses Neo4j as the graph store.", "project"),
            ("Aarya's thesis is due in March.", "project"),
        )
    )

    decision = await decide(
        provider,
        user_message="right, and the thesis is due in March",
        assistant_message="noted",
        subject_name="Aarya",
        known_facts=["Aarya uses Neo4j as the graph store."],
    )

    assert [f.statement for f in decision.facts] == ["Aarya's thesis is due in March."]


async def test_a_recall_turn_leaves_no_provenance_row(db_client, session_factory):
    """End to end: the exact turn that produced duplicate Neo4j facts."""
    project_id, chat_id = await _seed_chat(db_client)
    turn = (
        await db_client.post(f"/chats/{chat_id}/messages", json={"content": "hello there"})
    ).json()
    memory = FakeMemory(IngestResult(ok=True))

    await run_write_gate(
        session_factory=session_factory,
        provider=ScriptedProvider(
            _gate_json("Aarya uses Neo4j as the graph store.", "project")
        ),
        memory=memory,
        gate_model="gate",
        chat_id=uuid.UUID(chat_id),
        project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="What am I using for the graph store?",
        assistant_message="You're using Neo4j as the graph store.",
        recalled_facts=["Aarya uses Neo4j as the graph store."],
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MemoryEpisode).where(MemoryEpisode.chat_id == uuid.UUID(chat_id))
            )
        ).scalars().all()

    assert rows == []
    assert memory.calls == []



async def test_the_gate_asks_for_its_schema():
    """With the MLX runtime the schema is enforced while decoding, so the
    reply always parses; _parse stays the backstop for servers that ignore it."""
    from nexus_core.write_gate import GATE_SCHEMA

    provider = ScriptedProvider(_gate_json())
    await decide(
        provider,
        user_message="I moved to Pune last month.",
        assistant_message="Noted.",
        subject_name="Aarya",
    )

    fmt = provider.response_formats[0]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] is GATE_SCHEMA


async def test_the_gate_decides_at_temperature_zero():
    """At the runtime's default 0.7 the same introduction was kept twice and
    dropped once."""
    provider = ScriptedProvider(_gate_json("Aarya is a final year B.Tech student.", "user"))
    await decide(provider, user_message="I'm a final year B.Tech student", assistant_message="ok",
                 subject_name="Aarya")
    assert provider.temperatures == [0.0]


# ── A change of mind retires what it replaced ────────────────────────────────


def test_the_gate_names_what_a_correction_replaces():
    decision = _parse(_gate_json("The deadline is the 21st of November.", "project", replaces=[1, 3]))
    assert decision.replaces == (1, 3)
    # Even with nothing new to keep: "I no longer use Neo4j".
    retraction = _parse(json.dumps({"persist": False, "reason": "dropped Neo4j", "facts": [],
                                    "replaces": [2]}))
    assert retraction.persist is False and retraction.replaces == (2,)


async def _change_of_mind(db_client, session_factory, gate_reply: str, known):
    from nexus_core.memory_client import Fact
    from nexus_core.models import GateDecisionRecord

    project_id, chat_id = await _seed_chat(db_client)
    turn = (await db_client.post(f"/chats/{chat_id}/messages",
                                 json={"content": "The deadline has changed to the 21st"})).json()
    memory = FakeMemory(IngestResult(ok=True, group_id="p_x"),
                        known=[Fact(id=i, fact=t, scope="project") for i, t in known])
    await run_write_gate(
        session_factory=session_factory, provider=ScriptedProvider(gate_reply), memory=memory,
        gate_model="gate", chat_id=uuid.UUID(chat_id), project_id=uuid.UUID(project_id),
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        message_id=uuid.UUID(turn["user_message"]["id"]),
        user_message="The deadline has changed to the 21st", assistant_message="Updated.",
    )
    async with session_factory() as session:
        record = (await session.execute(select(GateDecisionRecord).where(
            GateDecisionRecord.chat_id == uuid.UUID(chat_id)))).scalars().one()
    return memory, record


async def test_a_correction_retires_the_old_fact_after_writing_the_new(db_client, session_factory):
    memory, record = await _change_of_mind(
        db_client, session_factory,
        _gate_json("The project deadline is the 21st of November.", "project", replaces=[1]),
        known=[("n14", "The project deadline is the 14th of November."), ("n-name", "Aarya is a student.")],
    )
    assert [c["statement"] for c in memory.calls] == ["The project deadline is the 21st of November."]
    assert memory.retired == [["The project deadline is the 14th of November."]]
    assert record.retired == [{"fact": "The project deadline is the 14th of November.", "id": "n14"},
                              {"items": ["item-0"]}]


async def test_a_number_outside_the_list_retires_nothing(db_client, session_factory):
    memory, record = await _change_of_mind(
        db_client, session_factory,
        _gate_json("The project deadline is the 21st of November.", "project", replaces=[7]),
        known=[("n14", "The project deadline is the 14th of November.")],
    )
    assert memory.retired == [] and record.retired == []
