"""The only code in core that talks to the open internet.

A web search is the one place where what someone typed leaves the machine, so
this module is where "client data stays here" is either kept or broken. Flag 1
in .github/IMPORTANT_BUSINESS_LOGIC_FLAGS.md settled where redaction lives --
here, inside the client, so no caller can forget it -- and left the mechanism
open. This uses both of the ones discussed there, in order:

1. A model call rewrites the message as a generic topic query. This is what
   catches a third party's name ("[patient]'s stage 3 lymphoma" becomes
   "stage 3 lymphoma treatment options"): nothing rule-based can know that an
   arbitrary word is someone's name.
2. Whatever the model returned, a deterministic pass strips what the system
   can vouch for: the signed-in person's own names and email, and anything
   carrying five or more digits (case numbers, MRNs, phone numbers). It runs
   on the model's output, so a rewrite that leaks is still caught.

If the rewrite fails or comes back empty, nothing is sent. Searching with the
raw message is never the fallback.

Deliberately not the shared httpx client on app.state: that one carries the
service token for memory and RAG, and this one talks to the internet.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from .providers.base import Provider

logger = logging.getLogger(__name__)

# Phrases that mean "go to the web" when typed, matching the old orchestrator's
# trigger (orchestrator/app/router.py) so both behave the same while both exist.
WEB_TRIGGER = re.compile(
    r"search the internet|search the web|look (it|this) up online", re.IGNORECASE
)

MAX_RESULTS = 5
MAX_QUERY_CHARS = 200
SEARCH_TIMEOUT_SECONDS = 15.0

REWRITE_PROMPT = """\
Turn the user's message into a web search query about its general topic.

Return ONLY the query text: no quotes, no explanation.

Never include any person's name, initials, email, phone number, address,
employer, case number, record number, or any other detail that identifies a
specific person, client or matter. Keep only the subject a public search
engine needs: the condition, the legal concept, the technology, the event.

Example
  Message: search the internet for the latest treatment options for Priya
           Nair's stage 3 lymphoma, MRN 00482913
  Query:   latest treatment options stage 3 lymphoma
"""

_EMAIL = re.compile(r"\S+@\S+")
_TOKEN = re.compile(r"\S+")


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


def strip_identifiers(query: str, known_names: list[str]) -> tuple[str, list[str]]:
    """The deterministic layer. Returns the cleaned query and what it removed."""
    removed: list[str] = []

    def drop(match: re.Match) -> str:
        removed.append(match.group(0))
        return " "

    query = _EMAIL.sub(drop, query)

    # Each part of a name on its own, so "Aarya" goes even when the model kept
    # only the first name. Parts under three letters are skipped: stripping
    # every "Al" or "Li" would gut ordinary queries.
    parts = {p for name in known_names for p in re.split(r"[\s@._-]+", name) if len(p) >= 3}
    for part in sorted(parts, key=len, reverse=True):
        query = re.sub(rf"\b{re.escape(part)}(?:'s)?\b", drop, query, flags=re.IGNORECASE)

    def drop_numbered(match: re.Match) -> str:
        token = match.group(0)
        if sum(ch.isdigit() for ch in token) >= 5:
            removed.append(token)
            return " "
        return token

    query = _TOKEN.sub(drop_numbered, query)
    return " ".join(query.split())[:MAX_QUERY_CHARS], removed


class WebClient:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        # Injectable so tests can see exactly what would have left the machine.
        self._transport = transport

    async def search(
        self,
        message: str,
        *,
        provider: Provider,
        model: str | None,
        known_names: list[str],
    ) -> WebSearch:
        """Redact, then search. Never raises: a failed search degrades a turn."""
        try:
            rewritten = await provider.chat(
                [
                    {"role": "system", "content": REWRITE_PROMPT},
                    {"role": "user", "content": message},
                ],
                model=model,
            )
        except Exception as exc:
            logger.warning("Web query rewrite failed; not searching: %s", exc)
            return WebSearch(query_sent=None, error="could not prepare a safe query")

        query, removed = strip_identifiers(rewritten.strip().strip('"'), known_names)
        if not query:
            logger.info("Web search skipped: nothing left after redaction")
            return WebSearch(query_sent=None, error="nothing left to search after redaction")

        logger.info("web search query=%r stripped=%s", query, removed)
        try:
            async with httpx.AsyncClient(
                timeout=SEARCH_TIMEOUT_SECONDS, transport=self._transport
            ) as http:
                response = await http.get(
                    f"{self._base_url}/search", params={"q": query, "format": "json"}
                )
                response.raise_for_status()
                raw = response.json().get("results", [])
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)
            return WebSearch(
                query_sent=query, error=f"search failed: {exc.__class__.__name__}",
                stripped=tuple(removed),
            )

        return WebSearch(
            query_sent=query,
            results=tuple(
                WebResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
                for r in raw[:MAX_RESULTS]
            ),
            stripped=tuple(removed),
        )
