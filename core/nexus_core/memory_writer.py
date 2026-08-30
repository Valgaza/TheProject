"""Running the write gate after a turn, and recording what happened.

This is the whole automatic-memory loop: a turn finishes, the gate reads it,
and if anything is worth keeping it goes into one scope with a row in
``memory_episodes`` saying where it came from.

That table exists because graphiti 0.29 declares ``EpisodicNode.episode_metadata``
and never persists it, so the episode itself cannot carry its own provenance.
Without it there is no way to answer "which message produced this fact", which
is the first question anyone asks when memory says something strange.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from .memory_client import MemoryClient
from .models import MemoryEpisode, Profile
from .providers.base import Provider
from .write_gate import decide

logger = logging.getLogger(__name__)


async def run_write_gate(
    *,
    session_factory: async_sessionmaker,
    provider: Provider,
    memory: MemoryClient,
    gate_model: str | None,
    chat_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
) -> None:
    """Decide, write, record. Runs after the reply has been sent.

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
            model=gate_model,
        )

        if not decision.persist:
            # Deliberately not recorded in the database. Rejections are the
            # common case by design, and a table that grows a row per "thanks"
            # is a table nobody will read. The log line is the audit trail, and
            # it is the thing you actually grep while tuning the prompt.
            logger.info(
                "gate declined chat=%s reason=%s", chat_id, decision.reason
            )
            return

        target_project = project_id if decision.scope == "project" else None

        # Row first, uuid after. graphiti mints the episode uuid, so it is not
        # knowable until the write returns -- but recording the attempt before
        # making it means a crash mid-ingest still leaves evidence that
        # something was supposed to be here.
        async with session_factory() as session:
            episode = MemoryEpisode(
                chat_id=chat_id,
                message_id=message_id,
                scope=decision.scope,
                status="pending",
                reason=decision.reason,
            )
            session.add(episode)
            await session.commit()
            episode_id = episode.id

        result = await memory.ingest(
            decision.statement,
            scope=decision.scope,
            user_id=user_id,
            project_id=target_project,
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
            decision.scope,
            "ingested" if result.ok else "failed",
            decision.statement[:100],
        )
    except Exception:
        logger.exception("Write gate failed for chat=%s", chat_id)
