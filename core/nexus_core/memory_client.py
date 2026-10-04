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

# Ingestion runs after the reply has gone out, so it can afford to wait for
# graphiti's extraction, measured at 12-30s per episode.
INGEST_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class IngestResult:
    ok: bool
    group_id: str | None = None
    episode_uuid: uuid.UUID | None = None
    error: str | None = None


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

    async def ingest(
        self,
        statement: str,
        *,
        scope: str,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None,
    ) -> IngestResult:
        """Write one distilled statement into one scope, and wait for it.

        Waits because the caller is already off the request path and needs two
        things a fire-and-forget call cannot give it: whether the write actually
        landed, and the uuid graphiti minted for the episode. That uuid is the
        only link back from a fact in the graph to the message that produced it.
        """
        try:
            response = await self._http.post(
                f"{self._base_url}/ingest",
                json={
                    "text": statement,
                    "user_id": str(user_id),
                    "project_id": str(project_id) if project_id else None,
                    "scope": scope,
                    "wait": True,
                },
                timeout=INGEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Memory ingest failed: %s", exc)
            return IngestResult(ok=False, error=f"{exc.__class__.__name__}: {exc}")

        body = response.json()
        raw_uuid = body.get("episode_uuid")
        return IngestResult(
            ok=body.get("status") == "ingested",
            group_id=body.get("group_id"),
            episode_uuid=uuid.UUID(raw_uuid) if raw_uuid else None,
            error=body.get("error"),
        )

    async def maintenance(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None,
        view: str,
        dry_run: bool,
    ) -> dict:
        """Ask the memory service to tidy one view of the graph.

        Given the ingest timeout rather than the search one: a sweep reads every
        edge in a group and rebuilds its summaries, which is slower than a
        search and, like ingest, never has a user waiting on it.
        """
        response = await self._http.post(
            f"{self._base_url}/maintenance",
            json={
                "user_id": str(user_id),
                "project_id": str(project_id) if project_id else None,
                "view": view,
                "dry_run": dry_run,
            },
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    # ── The graph panel ──────────────────────────────────────────────────────
    # Proxied for the browser, which no longer reaches memory itself. These
    # raise rather than degrade: an empty graph and an unreachable service
    # look identical on screen, and the panel should be able to tell them apart.

    @staticmethod
    def _view_params(user_id: uuid.UUID, project_id: uuid.UUID | None, view: str) -> dict:
        params = {"user_id": str(user_id), "view": view}
        if project_id is not None:
            params["project_id"] = str(project_id)
        return params

    async def graph(self, *, user_id: uuid.UUID, project_id: uuid.UUID | None, view: str) -> dict:
        response = await self._http.get(
            f"{self._base_url}/graph", params=self._view_params(user_id, project_id, view)
        )
        response.raise_for_status()
        return response.json()

    async def delete_graph(
        self, *, user_id: uuid.UUID, project_id: uuid.UUID | None, view: str
    ) -> None:
        response = await self._http.delete(
            f"{self._base_url}/graph", params=self._view_params(user_id, project_id, view)
        )
        response.raise_for_status()

    def graph_socket_url(
        self, *, user_id: uuid.UUID, project_id: uuid.UUID | None, view: str
    ) -> str:
        base = "ws" + self._base_url.removeprefix("http")
        params = self._view_params(user_id, project_id, view)
        params.pop("user_id")
        return f"{base}/ws/graph/{user_id}?{httpx.QueryParams(params)}"
