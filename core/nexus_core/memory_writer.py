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

import dataclasses
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .memory_client import MemoryClient
from .models import GateDecisionRecord, MemoryEpisode, Profile, Project
from .providers.base import Provider
from .decisions import GATE_QUESTIONS, DecisionsClient, log_entry
from .write_gate import GateDecision, GateFact, decide, looks_trivial, replaced_terms, uncovered_words

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
    triples: list[dict] | None = None,
) -> bool:
    """Send one stored episode to the graph and record the outcome."""
    started = time.perf_counter()
    result = await memory.ingest(
        body,
        scope=scope,
        user_id=user_id,
        project_id=project_id if scope == "project" else None,
        facts=triples,
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


def _verbatim(fact: str, subject_name: str) -> bool:
    """A message kept in the person's own words: it may hold several facts,
    so it is never retired automatically (retiring "We're using PyTorch and
    the dataset is CheXpert" for a switch to JAX lost the dataset)."""
    return fact.startswith(f"{subject_name} said:")


def _plausibly_replaced(candidates, message: str, new_statements: list[str], subject_name: str) -> list:
    """The model's own "this replaces fact 3", kept only where it makes sense.
    Once it pointed at the supervisor fact for "we switched from PyTorch to
    JAX". A replaced fact must mention what the person switched away from, or,
    with no such words, share a word with what they said now."""
    terms = [t.split()[0].lower() for t in replaced_terms(message)]
    new_words = set(re.findall(r"[a-z0-9]{4,}", " ".join(new_statements + [message]).lower()))
    new_words -= set(re.findall(r"[a-z0-9]+", subject_name.lower()))
    kept = []
    for fact in candidates:
        text = fact.fact.lower()
        if _verbatim(fact.fact, subject_name):
            continue
        if terms:
            if any(re.search(rf"\b{re.escape(t)}\b", text) for t in terms):
                kept.append(fact)
        elif new_words & set(re.findall(r"[a-z0-9]{4,}", text)):
            kept.append(fact)
    if len(kept) < len(candidates):
        logger.info("ignored %d replacement(s) that didn't fit the change", len(candidates) - len(kept))
    return kept


# The decision model's "worth remembering" at or above which a turn the LLM
# declined is kept anyway, in the person's words. Its scores are saturated: real
# facts sit near 0.98 (docs/benchmarks/laya-decision.md).
KEEP_ANYWAY_ABOVE = 0.9

# Words that make a fact about the work, not the person, in a project chat.
_WORK = re.compile(
    r"\b(thesis|project|startup|company|product|beta|launch|release|deadline|supervisor|advisor|"
    r"co-?founder|client|customer|teammate|team|colleague|investor|seed|funding|stack|dataset|repo)\b",
    re.IGNORECASE)


async def _rescope(facts: Sequence[GateFact], project_name: str | None, decisions) -> list[GateFact]:
    """In a project chat, move back to the project what the model filed under
    the person. Its mistakes went one way: "Meera is using JAX for her
    thesis" and "Arjun is Meera's co-founder", said in project chats, filed
    as personal, and so answered in every other chat. A fact naming the
    project or the work moves; otherwise the decision model's tier call
    decides (98% on labelled turns; its own misses were the same kind of
    fact, which the words catch)."""
    out = []
    for fact in facts:
        scope = fact.scope
        if scope == "user":
            names_work = _WORK.search(fact.statement) or (
                project_name and re.search(rf"\b{re.escape(project_name)}\b", fact.statement, re.IGNORECASE))
            if names_work:
                scope = "project"
            elif decisions is not None:
                d = await decisions.decide({"person_said": fact.statement}, {"tier": GATE_QUESTIONS["tier"]})
                if d is not None and d.label("tier") == "project":
                    scope = "project"
            if scope != fact.scope:
                logger.info("rescoped to the project: %s", fact.statement[:80])
        out.append(dataclasses.replace(fact, scope=scope))
    return out


async def _retire(session_factory, memory: MemoryClient, decision_id: uuid.UUID,
                  facts: Sequence, user_id: uuid.UUID, project_id: uuid.UUID | None) -> None:
    """Retire what a change of mind replaced, and record it on the turn."""
    if not facts:
        return
    items = await memory.retire([f.fact for f in facts], user_id=user_id, project_id=project_id)
    logger.info("gate retired %d graph items for %d replaced facts: %s", len(items), len(facts),
                [f.fact[:60] for f in facts])
    if not items:
        return
    async with session_factory() as session:
        record = await session.get(GateDecisionRecord, decision_id)
        if record is not None:
            record.retired = [{"fact": f.fact, "id": f.id} for f in facts] + [{"items": items}]
            await session.commit()


async def process_decision(
    *,
    session_factory: async_sessionmaker,
    provider: Provider,
    memory: MemoryClient,
    gate_model: str | None,
    decision_id: uuid.UUID,
    decisions: DecisionsClient | None = None,
    laya_gate: str = "off",
    laya_skip_below: float = 0.15,
) -> None:
    """Decide, write, record -- for one queued turn.

    With the decision model enabled for the gate (``laya_gate``), it is asked
    first. In shadow mode its answer is only recorded beside the LLM gate's;
    in live mode a confident "not worth remembering" skips the LLM call
    entirely, which is the common case -- most turns leave nothing behind.

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
        said_at = record.created_at
        recalled = list(record.recalled_facts or [])

    # What memory holds on this subject now, with ids: the gate must not
    # restate any of it, and names the ones a change of mind makes untrue so
    # they can be retired. Searched here rather than taken from the turn's
    # recall, which has no ids and predates any turn queued ahead of this one.
    # Falls back to that recall (without ids, so nothing can be retired).
    known = await memory.search(user_message, user_id=user_id, project_id=project_id)
    known_texts = [f.fact for f in known] if known else recalled

    project_name = None
    if project_id is not None:
        async with session_factory() as session:
            project = await session.get(Project, project_id)
            project_name = getattr(project, "name", None)

    started = time.perf_counter()
    laya = None
    laya_state = {"person_said": user_message, "assistant_replied": assistant_message[:400]}
    ask_laya = decisions is not None and laya_gate in ("shadow", "live") and not looks_trivial(user_message)
    if ask_laya:
        laya = await decisions.decide(laya_state, GATE_QUESTIONS)
    worth = laya.p("worth_remembering") if laya else None

    # A date or "remember" means the person is telling us something, however
    # it's phrased: "Can you remember that my driving test is on 9 November?"
    # scored as a question and was skipped (2026-10-13).
    from .clock import dates_in, relative_dates

    today = (said_at or _now()).astimezone().date()
    tells_something = bool(re.search(r"\bremember\b", user_message, re.IGNORECASE)
                           or dates_in(user_message, today) or relative_dates(user_message, today))
    if laya_gate == "live" and worth is not None and worth < laya_skip_below and not tells_something:
        decision = GateDecision.skip(f"decision model: not worth remembering (p={worth:.2f})")
        decided_by = "laya"
    else:
        decision = await decide(
            provider,
            user_message=user_message,
            assistant_message=assistant_message,
            subject_name=subject_name,
            known_facts=known_texts,
            model=gate_model,
            said_at=said_at,
            project_name=project_name,
        )
        decided_by = "prefilter" if decision.reason.startswith(PREFILTER_PREFIX) else "llm"
        if not decision.persist and decided_by == "llm" and worth is not None and worth >= KEEP_ANYWAY_ABOVE:
            # The LLM declined a turn the decision model is sure holds a
            # lasting fact: it did so once in eight identical runs for "Our
            # seed round target is 50 lakh rupees". Keep the words rather than
            # lose them; the tier is the decision model's call.
            scope = "project" if project_id is not None and laya.label("tier") == "project" else "user"
            decision = GateDecision(persist=True, reason=f"kept in their words; the model declined ({decision.reason})",
                                    facts=(GateFact(f'{subject_name} said: "{" ".join(user_message.split())}"', scope),),
                                    replaces=decision.replaces)
            decided_by = "laya"
    gate_ms = round((time.perf_counter() - started) * 1000)

    if decision.persist and project_id is not None:
        decision = GateDecision(persist=True, reason=decision.reason, replaces=decision.replaces,
                                facts=tuple(await _rescope(decision.facts, project_name, decisions)))
    tiers = _by_tier(decision.facts, project_id, chat_id) if decision.persist else {}
    if tiers:
        # Nothing the person said is lost to a model that summarised too
        # hard: "it's a recycling marketplace" kept as "GreenLoop is a
        # startup". Their own words go in too, in the tier the facts went to,
        # so recall can still find the detail (memory's ledger stores each line).
        missing = uncovered_words(user_message, [f.statement for f in decision.facts])
        if missing:
            scope = "project" if "project" in tiers else "user"
            tiers[scope].append(f'{subject_name} said: "{" ".join(user_message.split())}"')
            logger.info("gate facts left out %s; keeping the message too", missing[:6])
    episodes: list[tuple[uuid.UUID, str, str]] = []
    async with session_factory() as session:
        record = await session.get(GateDecisionRecord, decision_id)
        if record is None:
            # The chat was deleted while the gate was deciding (its rows go
            # with it): nothing left to record against, and nothing to write.
            logger.info("gate: turn %s vanished while being decided; dropping it", decision_id)
            return
        record.persist = bool(decision.persist)
        record.facts = [{"statement": f.statement, "scope": f.scope, "subject": f.subject,
                         "relation": f.relation, "object": f.object} for f in decision.facts]
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
        if ask_laya:
            # The LLM's verdict is the baseline Laya is measured against; when
            # Laya decided alone there is none, and the row says so.
            session.add(log_entry(
                point="gate", mode=laya_gate, state=laya_state, decision=laya,
                baseline=None if decided_by == "laya" else {
                    "persist": bool(decision.persist),
                    "tiers": sorted({f.scope for f in decision.facts}),
                },
                chat_id=chat_id, message_id=message_id,
            ))
        await session.commit()

    # What this turn made untrue, by id. Only from the searched facts: the
    # fallback recall has no ids to retire by.
    superseded = _plausibly_replaced(
        [known[n - 1] for n in decision.replaces if known and n <= len(known)],
        user_message, [f.statement for f in decision.facts], subject_name)
    if not superseded and known:
        # "Switched from PyTorch to JAX" with nothing named to replace: every
        # known fact that mentions PyTorch is the old value.
        terms = [t.split()[0].lower() for t in replaced_terms(user_message)]
        superseded = [f for f in known if not _verbatim(f.fact, subject_name)
                      and any(re.search(rf"\b{re.escape(t)}\b", f.fact.lower()) for t in terms)]
        if superseded:
            logger.info("change of mind %s retires %d known facts", terms, len(superseded))

    if not decision.persist:
        logger.info("gate declined chat=%s by=%s gate_ms=%d reason=%s",
                    chat_id, decided_by, gate_ms, decision.reason)
        # "I no longer use Neo4j": nothing new to write, but something to retire.
        await _retire(session_factory, memory, decision_id, superseded, user_id, project_id)
        return
    logger.info("gate accepted chat=%s gate_ms=%d facts=%d episodes=%d",
                chat_id, gate_ms, len(decision.facts), len(episodes))

    # Sequential, not gathered: two ingests against the same group race on
    # entity resolution (the same entity created twice).
    written: set[tuple[str, uuid.UUID | None]] = set()
    for episode_id, scope, body in episodes:
        lines = set(body.splitlines())
        ok = await _ingest_episode(
            session_factory=session_factory, memory=memory, episode_id=episode_id,
            body=body, scope=scope, user_id=user_id, project_id=project_id,
            triples=[{"statement": f.statement, "subject": f.subject, "relation": f.relation, "object": f.object}
                     for f in decision.facts if f.statement in lines and f.subject and f.object],
        )
        if ok:
            written.add((scope, project_id if scope == "project" else None))

    # After the new fact is in, never before: a question asked in between
    # should find the old value rather than nothing. And only if it landed.
    if written:
        await _retire(session_factory, memory, decision_id, superseded, user_id, project_id)

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
