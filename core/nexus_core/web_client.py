"""Core's view of private web search (SearXNG).

Same degrade-gracefully contract as memory_client.py and rag_client.py: an
unreachable SearXNG degrades an answer, it never fails one. The one real
difference from memory and RAG: this never runs automatically. Everything
else in this system works entirely offline; web search is the deliberate
exception, and it should only ever fire when a turn actually asked for it
(see wants_web_search() in orchestrator.py) -- not on every message, the
way memory search and RAG's existence check do.

Ported from the standalone orchestrator/ scaffold's app/clients/web_client.py
2026-09-03, once the rest of that scaffold (its own RAG/graph clients) had
already been superseded by core's real implementations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .retry import with_retry

logger = logging.getLogger(__name__)

# A live network call to a search engine, not a local lookup -- generous
# compared to memory/RAG's timeouts, but still bounded so a slow search
# provider can't hang the whole turn.
SEARCH_TIMEOUT_SECONDS = 15.0

# SearXNG can return dozens of results per query; most of that would just be
# wasted context tokens, so only the top few actually reach the prompt.
MAX_RESULTS = 5


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    snippet: str


class WebClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str) -> tuple[WebSource, ...] | None:
        """Web results for a query, or None on any failure -- same contract
        as MemoryClient.search()/RagClient.query(): the caller degrades the
        answer, it never fails the turn over this."""
        try:
            return await with_retry(lambda: self._search_once(query), name="web")
        except Exception as exc:
            logger.warning("Web search failed, answering without it: %s", exc)
            return None

    async def _search_once(self, query: str) -> tuple[WebSource, ...]:
        response = await self._http.get(
            f"{self._base_url}/search",
            # format=json is required -- SearXNG only returns an HTML page
            # by default; the json format is enabled explicitly in
            # docker/searxng/settings.yml.
            params={"q": query, "format": "json"},
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        # The JSON response has a "results" list among other fields
        # (suggestions, answers, ...) that aren't needed here.
        results = response.json().get("results", [])
        return tuple(
            WebSource(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in results[:MAX_RESULTS]
        )
