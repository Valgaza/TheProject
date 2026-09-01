"""Running the write gate after a turn, and recording what happened.

This is the whole automatic-memory loop: a turn finishes, the gate reads it,
and whatever is worth keeping goes into one scope apiece with a row in
``memory_episodes`` saying where it came from.

That table exists because graphiti 0.29 declares ``EpisodicNode.episode_metadata``
and never persists it, so the episode itself cannot carry its own provenance.
Without it there is no way to answer "which message produced this fact", which
is the first question anyone asks when memory says something strange.
"""

from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import async_sessionmaker

from .memory_client import MemoryClient
from .models import MemoryEpisode, Profile
from .providers.base import Provider
from .write_gate import GateFact, decide

logger = logging.getLogger(__name__)


async def _persist(
    *,
    session_factory: async_sessionmaker,
    memory: MemoryClient,
    fact: GateFact,
    reason: str,
    chat_id: uuid.UUID,
    project_id: uuid.UUID | None,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
) -> str | None:
    """Write one fact, and record the attempt either way.

    Returns the tier it landed in, which is not always the tier the gate
    chose -- a project fact in a chat with no project is written to the user
    tier instead. The caller needs the real answer to know what to tidy.
    """
    scope = fact.scope
    if scope == "project" and project_id is None:
        # A chat outside every project has no project tier to write to. The
        # fact is still worth keeping, so it goes to the user tier rather than
        # being dropped -- but this is the one place a scope decision is
        # overruled, so it says so out loud.
        logger.info(
            "gate said project for a chat with no project; writing to user "
            "tier instead  chat=%s statement=%r",
            chat_id,
            fact.statement[:80],
        )
        scope = "user"

    # Row first, uuid after. graphiti mints the episode uuid, so it is not
    # knowable until the write returns -- but recording the attempt before
    # making it means a crash mid-ingest still leaves evidence that something
    # was supposed to be here.
    async with session_factory() as session:
        episode = MemoryEpisode(
            chat_id=chat_id,
            message_id=message_id,
            scope=scope,
            status="pending",
            reason=reason,
        )
        session.add(episode)
        await session.commit()
        episode_id = episode.id

    result = await memory.ingest(
        fact.statement,
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
            await session.commit()

    logger.info(
        "gate persisted chat=%s scope=%s status=%s statement=%r",
        chat_id,
        scope,
        "ingested" if result.ok else "failed",
        fact.statement[:100],
    )
    return scope if result.ok else None


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
    """Decide, write, record. Runs after the reply has been sent.

    ``recalled_facts`` is what memory supplied for this same turn. It is handed
    to the gate as already-known, which is what stops the loop where asking
    about a fact causes it to be written again, more loudly, every time.

    Swallows everything. This is a background task: an exception here would be
    logged by the framework and otherwise vanish, so it is caught where there is
    enough context to say something useful about it.
    """
    try:
        # The gate needs a name to write facts about. Without one it produces
        # statements like "The user prefers terse answers", and graphiti turns
        # that into an entity literally called "The user" -- the same junk node
        # that the episode-body prefix used to create.
        async with session_factory() as session:
            profile = await session.get(Profile, user_id)
            subject_name = profile.display_name if profile else "the person"

        decision = await decide(
            provider,
            user_message=user_message,
            assistant_message=assistant_message,
            subject_name=subject_name,
            known_facts=recalled_facts,
            model=gate_model,
        )

        if not decision.persist:
            # Deliberately not recorded in the database. Rejections are the
            # common case by design, and a table that grows a row per "thanks"
            # is a table nobody will read. The log line is the audit trail, and
            # it is the thing you actually grep while tuning the prompt.
            logger.info("gate declined chat=%s reason=%s", chat_id, decision.reason)
            return

        # Sequential, not gathered. Each ingest is 12-30s of graphiti work
        # against the same group, and running two at once races on entity
        # resolution -- the same entity gets created twice because neither
        # write can see the other's node yet.
        written: set[tuple[str, uuid.UUID | None]] = set()
        for fact in decision.facts:
            scope = await _persist(
                session_factory=session_factory,
                memory=memory,
                fact=fact,
                reason=decision.reason,
                chat_id=chat_id,
                project_id=project_id,
                user_id=user_id,
                message_id=message_id,
            )
            if scope is not None:
                written.add((scope, project_id if scope == "project" else None))

        # Tidy each tier that took a write, if it has taken enough of them.
        # Imported here rather than at module scope: the maintenance router
        # imports this module's caller, and the cycle is not worth untangling
        # for one call.
        from .routers.maintenance import maybe_sweep

        for scope, target in written:
            await maybe_sweep(
                session_factory=session_factory,
                memory=memory,
                user_id=user_id,
                project_id=target,
                scope=scope,
            )
    except Exception:
        logger.exception("Write gate failed for chat=%s", chat_id)
