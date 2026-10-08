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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence, TypedDict

from .web_client import WEB_TRIGGER

if TYPE_CHECKING:
    from .rag_client import RagClient

logger = logging.getLogger(__name__)

Handler = Literal["general", "rag", "web", "vision", "speech"]


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
    use_web: bool = False,
    accepted_web_offer: bool = False,
    web_intent_p: float | None = None,
    web_intent_above: float = 0.9,
) -> Route:
    """Decide which handler answers this turn.

    Deliberately not a model call. Cheap deterministic signals -- an attached
    image, attached audio -- decide before anything reaches a model, and only
    genuinely ambiguous turns should ever cost a classification.

    Narrower than it was: documents left this function on 2026-09-03 and
    became a retrieval tier beside memory, gated by a relevance floor per
    turn rather than a corpus check per project. What belongs here is work
    that changes *which model runs*; what does not is work that only changes
    *what the model is told*.

    Still async, and still takes ``rag``, so that a future signal needing a
    network call does not have to change every caller back again.
    """
    # Documents used to be decided here, by asking whether any existed. They
    # are not any more: retrieval runs on every turn and a relevance floor in
    # chat_service decides per turn whether the corpus had anything to say.
    # Asking "does this project have documents" could only ever answer a
    # question about the project, never about the question being asked.
    #
    # What remains here is genuinely different work -- an attached image needs
    # a vision model, audio needs speech. Those cannot be decided by a
    # threshold after the fact, because they change which model runs at all.
    del rag  # kept in the signature; callers still pass it

    # The web belongs here for the same reason: it is the one handler that
    # sends something off the machine, so it only ever runs when the person
    # asked for it -- the composer's web button, or saying so in words. Never
    # because a model judged a search would help.
    #
    # "Saying so in words" has two detectors. The trigger phrases catch the
    # obvious wording; ``web_intent_p``, when given, is the decision model's
    # probability that the message asks for the web in other words ("can you
    # check online…"). It is passed only when that point is live, and can only
    # turn the web on, never off, and only at a high bar.
    if use_web:
        route = Route(handler="web", reason="web search requested from the composer")
    elif WEB_TRIGGER.search(content):
        route = Route(handler="web", reason="the message asks to search the web")
    elif accepted_web_offer:
        # Asked, in two steps: Nexus offered to check online, and the person
        # said yes. The search is for the question that prompted the offer.
        route = Route(handler="web", reason="the person said yes to checking online")
    elif web_intent_p is not None and web_intent_p >= web_intent_above:
        route = Route(
            handler="web",
            reason=f"the message asks to look something up online (decision model, p={web_intent_p:.2f})",
        )
    else:
        route = Route(handler="general", reason="no attachment requiring another pipeline")
    logger.info(
        "route handler=%s reason=%s attachments=%d",
        route.handler,
        route.reason,
        len(attachments),
    )
    return route
