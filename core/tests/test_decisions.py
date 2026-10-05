"""The decision model at each point: off, shadow (recorded, ignored) and live
(acted on, within its threshold) -- and the old logic whenever it is absent."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from nexus_core.decisions import Decision, runtime_root
from nexus_core.memory_client import IngestResult
from nexus_core.memory_writer import process_decision
from nexus_core.models import DecisionLogRecord, GateDecisionRecord
from nexus_core.write_queue import Activity, WriteQueue


class FakeDecisions:
    def __init__(self, answers: dict | None) -> None:
        self.answers = answers
        self.calls: list[tuple[dict, dict]] = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        return Decision(answers=self.answers, ms=12) if self.answers is not None else None


class FakeMemory:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, query, *, user_id, project_id=None, limit=None):
        return []

    async def ingest(self, statement, *, scope, user_id, project_id):
        self.calls.append(statement)
        return IngestResult(ok=True, group_id="g", episode_uuid=uuid.uuid4())


class CountingProvider:
    def __init__(self, reply: str) -> None:
        self.reply, self.calls = reply, 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return self.reply


def _gate(persist=True):
    return json.dumps({"persist": persist, "reason": "r",
                       "facts": [{"statement": "Aarya uses Neo4j.", "scope": "user"}] if persist else []})


def test_the_runtime_root_comes_from_the_openai_url():
    assert runtime_root("http://localhost:8003/v1") == "http://localhost:8003"
    # Ollama, the fallback, has no decision model.
    assert runtime_root("http://localhost:11434/v1") is None
    assert runtime_root(None) is None


# ── web intent ───────────────────────────────────────────────────────────────


def _settings(db_client, mode):
    s = db_client._transport.app.state.settings
    s.laya_web_intent, s.laya_web_intent_above = mode, 0.9


async def _turn(db_client, content):
    chat = (await db_client.post("/chats", json={})).json()
    return (await db_client.post(f"/chats/{chat['id']}/messages", json={"content": content})).json()


async def _log(session_factory, point, chat_id):
    """This test's rows only, oldest first: the database is shared with a
    running stack whose own decisions land in the same table."""
    async with session_factory() as session:
        return (await session.execute(
            select(DecisionLogRecord)
            .where(DecisionLogRecord.point == point, DecisionLogRecord.chat_id == uuid.UUID(str(chat_id)))
            .order_by(DecisionLogRecord.created_at)
        )).scalars().all()


async def test_live_web_intent_routes_a_paraphrased_request_to_the_web(db_client, session_factory):
    db_client._transport.app.state.decisions = FakeDecisions({"asks_for_web": {"p": 0.97}})
    _settings(db_client, "live")

    turn = await _turn(db_client, "can you check online what changed in raft lately?")

    assert turn["route"]["handler"] == "web"
    assert "decision model" in turn["route"]["reason"]
    rows = await _log(session_factory, "web_intent", turn["user_message"]["chat_id"])
    assert rows[-1].mode == "live" and rows[-1].laya["asks_for_web"]["p"] == 0.97


async def test_live_web_intent_below_the_bar_stays_local(db_client):
    db_client._transport.app.state.decisions = FakeDecisions({"asks_for_web": {"p": 0.6}})
    _settings(db_client, "live")

    turn = await _turn(db_client, "what do you think about raft?")
    assert turn["route"]["handler"] == "general"


async def test_shadow_web_intent_is_recorded_and_ignored(db_client, session_factory):
    db_client._transport.app.state.decisions = FakeDecisions({"asks_for_web": {"p": 0.99}})
    _settings(db_client, "shadow")

    turn = await _turn(db_client, "can you check online what changed in raft lately?")

    assert turn["route"]["handler"] == "general"
    row = (await _log(session_factory, "web_intent", turn["user_message"]["chat_id"]))[-1]
    assert row.mode == "shadow" and row.baseline["asks_for_web"] is False


async def test_the_trigger_phrase_never_asks_the_model(db_client):
    fake = FakeDecisions({"asks_for_web": {"p": 0.1}})
    db_client._transport.app.state.decisions = fake
    _settings(db_client, "live")

    turn = await _turn(db_client, "search the web for raft news")
    assert turn["route"]["handler"] == "web" and fake.calls == []


async def test_an_absent_decision_model_changes_nothing(db_client):
    db_client._transport.app.state.decisions = FakeDecisions(None)  # unreachable
    _settings(db_client, "live")
    assert (await _turn(db_client, "can you check online?"))["route"]["handler"] == "general"


# ── the write gate ───────────────────────────────────────────────────────────


async def _queued(db_client, session_factory, content="I switched the project to Qdrant."):
    db_client._transport.app.state.memory = FakeMemory()
    db_client._transport.app.state.decisions = None
    turn = await _turn(db_client, content)
    async with session_factory() as session:
        return (await session.execute(
            select(GateDecisionRecord).where(
                GateDecisionRecord.chat_id == uuid.UUID(turn["user_message"]["chat_id"]))
        )).scalars().one()


async def _process(session_factory, record, provider, decisions, mode):
    await process_decision(
        session_factory=session_factory, provider=provider, memory=FakeMemory(),
        gate_model="g", decision_id=record.id,
        decisions=decisions, laya_gate=mode, laya_skip_below=0.15,
    )
    async with session_factory() as session:
        return await session.get(GateDecisionRecord, record.id)


async def test_live_gate_skips_the_model_on_a_confident_no(db_client, session_factory):
    record = await _queued(db_client, session_factory, "what time zone is Lisbon in?")
    provider = CountingProvider(_gate())
    done = await _process(session_factory, record, provider,
                          FakeDecisions({"worth_remembering": {"p": 0.03}}), "live")

    assert provider.calls == 0
    assert (done.persist, done.decided_by) == (False, "laya")
    row = (await _log(session_factory, "gate", record.chat_id))[-1]
    assert row.baseline is None  # nothing to compare: the LLM never ran


async def test_live_gate_still_asks_the_model_when_laya_says_maybe(db_client, session_factory):
    record = await _queued(db_client, session_factory)
    provider = CountingProvider(_gate())
    done = await _process(session_factory, record, provider,
                          FakeDecisions({"worth_remembering": {"p": 0.7}}), "live")

    assert provider.calls == 1 and done.persist is True and done.decided_by == "llm"


async def test_shadow_gate_records_both_verdicts(db_client, session_factory):
    record = await _queued(db_client, session_factory)
    provider = CountingProvider(_gate())
    done = await _process(session_factory, record, provider,
                          FakeDecisions({"worth_remembering": {"p": 0.02}, "tier": {"label": "user"}}),
                          "shadow")

    assert provider.calls == 1 and done.decided_by == "llm"  # Laya ignored
    row = (await _log(session_factory, "gate", record.chat_id))[-1]
    assert row.mode == "shadow"
    assert row.laya["worth_remembering"]["p"] == 0.02
    assert row.baseline == {"persist": True, "tiers": ["user"]}


async def test_a_pleasantry_never_reaches_the_decision_model(db_client, session_factory):
    record = await _queued(db_client, session_factory, "thanks!")
    fake = FakeDecisions({"worth_remembering": {"p": 0.9}})
    done = await _process(session_factory, record, CountingProvider(_gate()), fake, "live")
    assert fake.calls == [] and done.decided_by == "prefilter"


async def test_the_queue_passes_its_decision_settings_on(db_client, session_factory):
    record = await _queued(db_client, session_factory, "what time zone is Lisbon in?")
    provider = CountingProvider(_gate())
    queue = WriteQueue(
        session_factory=session_factory, provider=provider, memory=FakeMemory(), gate_model="g",
        activity=Activity(), idle_grace=0.0, poll_seconds=0.05,
        decisions=FakeDecisions({"worth_remembering": {"p": 0.01}}), laya_gate="live",
    )
    await queue.drain_once()
    async with session_factory() as session:
        assert (await session.get(GateDecisionRecord, record.id)).decided_by == "laya"
    assert provider.calls == 0
