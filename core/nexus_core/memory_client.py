"""Core's view of the memory service.

Kept deliberately thin and deliberately forgiving: memory being unreachable
degrades an answer, it must never fail one. A model that answers without
recalled context is still useful; an error page is not.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Retrieval sits in front of a user waiting for a reply, so it gets a short
# leash. Better to answer without memory than to make someone wait for it.
SEARCH_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class Fact:
    id: str
    fact: str
    scope: str
    # "relationship" (from a graph edge) or "entity" (from a node's summary).
    kind: str = "relationship"
    subject: str | None = None

    def render(self) -> str:
        """One line, tagged with the tier it came from.

        The tag is there for the model as much as the reader: a durable fact
        about the person and an incidental project detail deserve different
        weight, and the model cannot tell them apart from the text alone.
        """
        return f"[{self.scope}] {self.fact}"


class MemoryClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        limit: int | None = None,
    ) -> list[Fact]:
        """Facts relevant to a query. Returns [] on any failure, by design."""
        payload: dict = {
            "query": query,
            "user_id": str(user_id),
            "project_id": str(project_id) if project_id else None,
        }
        if limit is not None:
            payload["limit"] = limit

        try:
            response = await self._http.post(
                f"{self._base_url}/search",
                json=payload,
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            # Warning, not exception: an unreachable memory service during
            # local development is routine, and a stack trace per turn would
            # bury the logs this phase exists to let you read.
            logger.warning("Memory search failed, answering without it: %s", exc)
            return []

        body = response.json()
        return [
            Fact(
                id=item["id"],
                fact=item["fact"],
                scope=item["scope"],
                kind=item.get("kind", "relationship"),
                subject=item.get("subject"),
            )
            for item in body.get("facts", [])
        ]
