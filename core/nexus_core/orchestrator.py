"""Route a turn to the handler that should answer it.

This is the seam the other pipelines attach to. Right now there is exactly one
handler and ``select_route`` always picks it, but the signature is the real one:
RAG and vision land here as extra branches, not as a rewrite of the caller.

The user never picks a pipeline. They type into one composer and optionally
attach something; the routing decision is ours to make and ours to explain,
which is why every route carries a ``reason`` — it is what the UI shows and
what you read in the logs when a route looks wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Sequence, TypedDict

logger = logging.getLogger(__name__)

Handler = Literal["general", "rag", "vision", "speech"]


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


def select_route(
    *,
    content: str,
    attachments: Sequence[Attachment] = (),
) -> Route:
    """Decide which handler answers this turn.

    Deliberately not a model call. Cheap deterministic signals — an attached
    image, a project with an indexed corpus — should decide before anything
    reaches a model, and only genuinely ambiguous turns should cost a
    classification. Today every turn is unambiguous because there is one
    handler.
    """
    route = Route(handler="general", reason="only the general handler is implemented")
    logger.info(
        "route handler=%s reason=%s attachments=%d",
        route.handler,
        route.reason,
        len(attachments),
    )
    return route
