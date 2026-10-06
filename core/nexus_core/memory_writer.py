"""Running the write gate on a finished turn, and recording what happened.

This is the whole automatic-memory loop: a turn finishes and is queued as a
``gate_decisions`` row; the write worker (write_queue.py) picks it up, the
gate reads it, and whatever is worth keeping goes into the graph -- one
episode per tier per turn, each with a ``memory_episodes`` row saying where it
came from.

That table exists because graphiti 0.29 declares ``EpisodicNode.episode_metadata``
and never persists it, so the episode itself cannot carry its own provenance.
Without it there is no way to answer "which message produced this fact", which
is the first question anyone asks when memory says something strange.

One episode per tier rather than one per fact: graphiti's extraction costs
several model calls per episode whatever its length, so three facts from one
turn cost a third as much as one episode each, and they arrive together, as
they were said.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .memory_client import MemoryClient
from .models import GateDecisionRecord, MemoryEpisode, Profile
from .providers.base import Provider
from .write_gate import GateFact, decide

logger = logging.getLogger(__name__)

PREFILTER_PREFIX = "pre-filtered"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_turn(
    session: AsyncSession,
    *,
    chat_id: uuid.UUID,
    project_id: uuid.UUID | None,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
    recalled_facts: Sequence[str] = (),
) -> GateDecisionRecord:
    """Queue a finished turn for the gate, in the caller's transaction.

    Added to the same session that stores the turn, so the two commit
    together: a turn is never stored without its queue entry, and the queue
    never points at a message that does not exist yet.

    ``recalled_facts`` is what memory supplied for this same turn. The gate is
    told them as already known, which is what stops the loop where asking about
    a fact writes it again, more loudly, every time.
    """
    record = GateDecisionRecord(
        chat_id=chat_id,
        project_id=project_id,
        user_id=user_id,
        message_id=message_id,
        user_message=user_message,
        assistant_message=assistant_message,
        recalled_facts=list(recalled_facts),
        status="pending",
    )
    session.add(record)
    return record


async def _ingest_episode(
    *,
    session_factory: async_sessionmaker,
    memory: MemoryClient,
    episode_id: uuid.UUID,
    body: str,
    scope: str,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
) -> bool:
    """Send one stored episode to the graph and record the outcome."""
    started = time.perf_counter()
    result = await memory.ingest(
        body,
        scope=scope,
        user_id=user_id,
        project_id=project_id if scope == "project" else None,
    )
    async with session_factory() as session:
        stored = await session.get(MemoryEpisode, episode_id)
        if stored is not None:
            stored.status = "ingested" if result.ok else "failed"
            stored.episode_uuid = result.episode_uuid
            stored.error = result.error
            stored.updated_at = _now()
            await session.commit()
    logger.info(
        "gate persisted episode=%s scope=%s status=%s ingest_ms=%d body=%r",
        episode_id, scope, "ingested" if result.ok else "failed",
        round((time.perf_counter() - started) * 1000), body[:100],
    )
    return result.ok


def _by_tier(facts: Sequence[GateFact], project_id: uuid.UUID | None, chat_id) -> dict[str, list[str]]:
    """Group the kept facts by the tier they will actually land in.

    A chat outside every project has no project tier to write to; such a fact
    is still worth keeping, so it goes to the user tier rather than being
    dropped. This is the one place a scope decision is overruled, so it says
    so out loud.
    """
    tiers: dict[str, list[str]] = {}
    for fact in facts:
        scope = fact.scope
        if scope == "project" and project_id is None:
            logger.info(
                "gate said project for a chat with no project; writing to user "
                "tier instead  chat=%s statement=%r", chat_id, fact.statement[:80],
            )
            scope = "user"
        tiers.setdefault(scope, []).append(fact.statement)
    return tiers


async def process_decision(
    *,
    session_factory: async_sessionmaker,
    provider: Provider,
    memory: MemoryClient,
    gate_model: str | None,
    decision_id: uuid.UUID,
) -> None:
    """Decide, write, record -- for one queued turn.

    Raises only on infrastructure failure (the database). The write queue
    catches that and retries the row; a gate or graph failure is an outcome,
    recorded on the row, not an exception.
    """
    async with session_factory() as session:
        record = await session.get(GateDecisionRecord, decision_id)
        if record is None:
            return
        # The gate needs a name to write facts about. Without one it produces
        # statements like "The user prefers terse answers", and graphiti turns
        # that into an entity literally called "The user".
        profile = await session.get(Profile, record.user_id)
        subject_name = profile.display_name if profile else "the person"
        chat_id, project_id, user_id, message_id = (
            record.chat_id, record.project_id, record.user_id, record.message_id
        )
        user_message, assistant_message = record.user_message, record.assistant_message
        recalled = list(record.recalled_facts or [])

    started = time.perf_counter()
    decision = await decide(
        provider,
        user_message=user_message,
        assistant_message=assistant_message,
        subject_name=subject_name,
        known_facts=recalled,
        model=gate_model,
    )
    decided_by = "prefilter" if decision.reason.startswith(PREFILTER_PREFIX) else "llm"
    gate_ms = round((time.perf_counter() - started) * 1000)

    tiers = _by_tier(decision.facts, project_id, chat_id) if decision.persist else {}
    episodes: list[tuple[uuid.UUID, str, str]] = []
    async with session_factory() as session:
        record = await session.get(GateDecisionRecord, decision_id)
        record.persist = bool(decision.persist)
        record.facts = [{"statement": f.statement, "scope": f.scope} for f in decision.facts]
        record.reason = decision.reason
        record.decided_by = decided_by
        record.gate_ms = gate_ms
        record.updated_at = _now()
        # Episode rows before the writes, uuids after: graphiti mints the
        # episode uuid, but recording the attempt first means a crash
        # mid-ingest still leaves evidence -- and the body to retry with.
        for scope, statements in tiers.items():
            episode = MemoryEpisode(
                chat_id=chat_id,
                message_id=message_id,
                decision_id=decision_id,
                scope=scope,
                status="pending",
                reason=decision.reason,
                body="\n".join(statements),
            )
            session.add(episode)
            await session.flush()
            episodes.append((episode.id, scope, episode.body))
        if not episodes:
            record.status = "done"
        await session.commit()

    if not decision.persist:
        logger.info("gate declined chat=%s by=%s gate_ms=%d reason=%s",
                    chat_id, decided_by, gate_ms, decision.reason)
        return
    logger.info("gate accepted chat=%s gate_ms=%d facts=%d episodes=%d",
                chat_id, gate_ms, len(decision.facts), len(episodes))

    # Sequential, not gathered: two ingests against the same group race on
    # entity resolution (the same entity created twice).
    written: set[tuple[str, uuid.UUID | None]] = set()
    for episode_id, scope, body in episodes:
        ok = await _ingest_episode(
            session_factory=session_factory, memory=memory, episode_id=episode_id,
            body=body, scope=scope, user_id=user_id, project_id=project_id,
        )
        if ok:
            written.add((scope, project_id if scope == "project" else None))

    async with session_factory() as session:
        record = await session.get(GateDecisionRecord, decision_id)
        record.status = "done"
        record.updated_at = _now()
        await session.commit()

    # Tidy each tier that took a write, if it has taken enough of them.
    # Imported here: the maintenance router imports this module's caller.
    from .routers.maintenance import maybe_sweep

    for scope, target in written:
        await maybe_sweep(
            session_factory=session_factory, memory=memory,
            user_id=user_id, project_id=target, scope=scope,
        )


async def resume_episode(
    *,
    session_factory: async_sessionmaker,
    memory: MemoryClient,
    episode_id: uuid.UUID,
) -> None:
    """Retry an episode left pending by a restart mid-ingest."""
    async with session_factory() as session:
        episode = await session.get(MemoryEpisode, episode_id)
        if episode is None or episode.status != "pending" or not episode.body:
            return
        record = (
            await session.get(GateDecisionRecord, episode.decision_id)
            if episode.decision_id else None
        )
        if record is None:
            return
        body, scope, user_id, project_id = (
            episode.body, episode.scope, record.user_id, record.project_id
        )
    await _ingest_episode(
        session_factory=session_factory, memory=memory, episode_id=episode_id,
        body=body, scope=scope, user_id=user_id, project_id=project_id,
    )


async def run_write_gate(
    *,
    session_factory: async_sessionmaker,
    provider: Provider,
    memory: MemoryClient,
    gate_model: str | None,
    chat_id: uuid.UUID,
    project_id: uuid.UUID | None,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
    recalled_facts: Sequence[str] = (),
) -> None:
    """Queue one turn and process it now, without the worker.

    For callers that need the outcome inline (tests, scripts). Swallows
    everything, as a background step must: an exception here would otherwise
    vanish without a word.
    """
    try:
        async with session_factory() as session:
            record = enqueue_turn(
                session, chat_id=chat_id, project_id=project_id, user_id=user_id,
                message_id=message_id, user_message=user_message,
                assistant_message=assistant_message, recalled_facts=recalled_facts,
            )
            await session.commit()
            decision_id = record.id
        await process_decision(
            session_factory=session_factory, provider=provider, memory=memory,
            gate_model=gate_model, decision_id=decision_id,
        )
    except Exception:
        logger.exception("Write gate failed for chat=%s", chat_id)
