"""Core's view of web search: a client for the websearch service.

Web search is the one place where what someone typed leaves the machine, so
it lives in its own service (websearch/, a container beside SearXNG): the
redaction -- a model rewrite, then deterministic stripping of the person's
names, emails and ID-like numbers, then the decision model's last check --
happens there, and so does the audit log of every query that left. SearXNG
itself is not reachable from the host at all. See flag 1 in
docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md.

What stays here is when to search (the trigger phrases, used by
orchestrator.py) and the shape core passes around.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Phrases that mean "go to the web" when typed. Inherited from the standalone
# orchestrator scaffold's router, deleted 2026-10-04.
WEB_TRIGGER = re.compile(
    r"search the internet|search the web|look (it|this) up online", re.IGNORECASE
)

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
        self, message: str, *, known_names: list[str], user_id: str | None = None
    ) -> WebSearch:
        """Ask the websearch service. Never raises: a failed search degrades
        a turn, it does not fail one."""
        try:
            response = await self._http.post(
                f"{self._base_url}/search",
                json={"message": message, "known_names": known_names, "user_id": user_id},
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
