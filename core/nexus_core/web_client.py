"""Core's view of web search: a client for the websearch service.

Web search is the one place where what someone typed leaves the machine, so
it lives in its own service (websearch/, a container beside SearXNG): the
redaction -- a model rewrite, then deterministic stripping of the person's
names, emails and ID-like numbers, then the decision model's last check --
happens there, and so does the audit log of every query that left. SearXNG
itself is not reachable from the host at all. See flag 1 in
docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md.

What stays here is when to search (the trigger phrases, and a yes to
Nexus's own offer to check, used by orchestrator.py and chat_service.py)
and the shape core passes around.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Phrases that mean "go to the web", typed or said. Asking for a search is
# the only way one runs (flag 1), so these lean to the explicit: a verb of
# looking plus the web, or "look it up" on its own -- not merely mentioning
# the internet ("I read online that...").
WEB_TRIGGER = re.compile(
    r"\b(?:search|check|google|browse)\b[^.?!]{0,30}?\b(?:online|(?:the )?(?:web|internet))\b"
    r"|\blook (?:it|this|that|them) up\b"
    r"|(?:^\W*|\b(?:can|could|would|will) you (?:please )?|\bplease )look up\b"
    r"|\blook up\b[^.?!]{0,40}?\b(?:online|on the (?:web|internet))\b"
    r"|\blook (?:online|on the (?:web|internet))\b"
    r"|\bgoogle (?:it|this|that)\b"
    r"|\b(?:use|reference|consult) (?:the )?(?:web|internet)\b"
    r"|\bwhat (?:does|do) (?:the )?(?:web|internet|people online) say\b",
    re.IGNORECASE,
)

# How an answer offers to search (the system prompt asks for exactly "Want me
# to check online?"; this allows for the model's paraphrase): a closing
# question about checking, looking up or searching, online.
WEB_OFFER = re.compile(
    r"\b(?:want|would you like|shall|should|do you want)\b[^?]{0,40}"
    r"\b(?:check|look|search|find)\b[^?]{0,40}"
    r"(?:\bonline\b|\bweb\b|\binternet\b|\bup\b)[^?]{0,20}\?[\s\"'*)]*$",
    re.IGNORECASE,
)

# A short yes, as a whole reply.
_YES = re.compile(
    r"(?:yes|yeah|yep|yup|ya|sure|please|ok(?:ay)?|alright|go ahead|do it|go for it|"
    r"absolutely|definitely|of course|please do|why not)\b(?:[ ,]+\w+){0,5}",
    re.IGNORECASE,
)


def offers_web(answer: str) -> bool:
    """Did this answer end by offering to check online?"""
    return bool(answer) and bool(WEB_OFFER.search(answer.strip()))


def accepts(reply: str) -> bool:
    """Is this reply a plain yes? A yes with a new question attached
    ("yes, but what about...") is a new turn, not consent."""
    words = " ".join(re.sub(r"[^\w\s']", " ", reply).split())
    if not words or len(words.split()) > 6 or re.search(r"\b(?:no|not|don'?t|nope)\b", words, re.I):
        return False
    return bool(_YES.fullmatch(words))

# Rewrite through the runtime plus a live search engine: generous, bounded.
SEARCH_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class WebSearch:
    """What happened, including the exact text that left the machine."""

    query_sent: str | None
    results: tuple[WebResult, ...] = ()
    error: str | None = None
    stripped: tuple[str, ...] = field(default=())
    blocked: bool = False


class WebClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        # The shared client: it carries the service token, which the
        # websearch service requires and never forwards to the internet.
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def search(
        self, message: str, *, known_names: list[str], user_id: str | None = None,
        context: list[str] = (),
    ) -> WebSearch:
        """Ask the websearch service. Never raises: a failed search degrades
        a turn, it does not fail one."""
        try:
            response = await self._http.post(
                f"{self._base_url}/search",
                json={"message": message, "known_names": known_names, "user_id": user_id,
                      "context": list(context)},
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            logger.warning("Web search service unavailable: %s", exc)
            return WebSearch(query_sent=None, error="the web search service is unreachable")
        logger.info("web search query_sent=%r stripped=%s blocked=%s",
                    body.get("query_sent"), body.get("stripped"), body.get("blocked"))
        return WebSearch(
            query_sent=body.get("query_sent"),
            results=tuple(WebResult(**r) for r in body.get("results", [])),
            error=body.get("error"),
            stripped=tuple(body.get("stripped", [])),
            blocked=bool(body.get("blocked")),
        )
