"""Route a turn to the handler that should answer it.

This is the seam the other pipelines attach to. RAG is the first real branch;
vision and speech land here the same way once those services exist.

The user never picks a pipeline. They type into one composer and optionally
attach something; the routing decision is ours to make and ours to explain,
which is why every route carries a ``reason`` — it is what the UI shows and
what you read in the logs when a route looks wrong.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence, TypedDict

if TYPE_CHECKING:
    from .rag_client import RagClient

logger = logging.getLogger(__name__)

Handler = Literal["general", "rag", "vision", "speech"]

# Matches these phrases anywhere in the message, ignoring case, so "Search
# the Internet for X" and "search the internet for x" both match. Ported
# from the standalone orchestrator/ scaffold's app/router.py.
_WEB_TRIGGER_PHRASES = re.compile(
    r"search the internet|search the web|look (it|this) up online",
    re.IGNORECASE,
)


def wants_web_search(content: str) -> bool:
    """Whether this turn should also pull in live web results.

    A cheap regex, deliberately not a model call -- same "cheap
    deterministic signal before anything reaches a model" philosophy as
    select_route() above. Independent of which handler answers: unlike RAG,
    web search isn't its own Handler, because it isn't mutually exclusive
    with one. It can fire alongside "general" or "rag" alike, the same way
    memory search always runs regardless of handler (see chat_service.py).
    Everything else in this system works offline; this is the deliberate,
    explicit-opt-in exception, so it only ever fires when asked for, never
    on an existence check the way RAG's routing does.
    """
    return bool(_WEB_TRIGGER_PHRASES.search(content))


class Attachment(TypedDict, total=False):
    """Something the user attached to a turn.

    Nothing produces these yet — the composer's attach and mic buttons are inert
    this milestone. The type exists so the handlers that consume attachments can
    be written against a settled shape rather than inventing one each.
    """

    kind: Literal["file", "image", "audio"]
    uri: str
    mime_type: str
    name: str


@dataclass(frozen=True)
class Route:
    handler: Handler
    reason: str


async def select_route(
    *,
    content: str,
    attachments: Sequence[Attachment] = (),
    rag: "RagClient | None" = None,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Route:
    """Decide which handler answers this turn.

    Deliberately not a model call. Cheap deterministic signals — an attached
    image, a project with an indexed corpus — should decide before anything
    reaches a model, and only genuinely ambiguous turns should cost a
    classification. Async now, unlike the milestone before this one: checking
    "does this project have RAG documents" is a real network call, not
    something a plain function can decide on its own.

    user_id is optional in the signature only so a caller with no RAG client
    at all doesn't need one either -- whenever rag is given, chat_service.py
    always has a real user_id in hand and passes it, since RAG is scoped per
    user/project the same way memory is.
    """
    if rag is not None and user_id is not None:
        # has_documents() already fails open (returns True) on its own errors —
        # nothing to catch here. If it says yes, route there; the actual query
        # (in chat_service) still degrades gracefully if RAG turns out to be
        # unreachable when it's really called a moment later.
        if await rag.has_documents(user_id=user_id, project_id=project_id):
            route = Route(handler="rag", reason="project has documents ingested in RAG")
            logger.info(
                "route handler=%s reason=%s attachments=%d",
                route.handler, route.reason, len(attachments),
            )
            return route
        reason = "no documents ingested in RAG"
    elif rag is None:
        reason = "no RAG client configured"
    else:
        reason = "no user_id given, cannot scope a RAG lookup"

    route = Route(handler="general", reason=reason)
    logger.info(
        "route handler=%s reason=%s attachments=%d",
        route.handler,
        route.reason,
        len(attachments),
    )
    return route
