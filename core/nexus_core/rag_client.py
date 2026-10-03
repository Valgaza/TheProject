"""Core's view of the RAG pipeline.

Same contract as memory_client.py: RAG being unreachable degrades an answer,
it must never fail one. The one real difference from memory is cost — a graph
lookup is a fast Neo4j query, but a RAG query embeds the question, runs a
hybrid search, and reranks the results, and the very first call after the RAG
API restarts also pays for loading its embedding/reranker models from disk.
QUERY_TIMEOUT_SECONDS reflects that; retrying on top (via retry.py)
reflects that a slow moment there is routine, not exceptional, so it's worth
one more try before giving up on this source for the turn.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx

from .retry import with_retry

logger = logging.getLogger(__name__)

# Retrieval sits in front of a user waiting for a reply. Longer than memory's
# 4s because embed + hybrid search + rerank is genuinely slower than a graph
# lookup, and a cold RAG API loads models on its first query.
QUERY_TIMEOUT_SECONDS = 30.0
STATUS_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RagSource:
    file_name: str
    page_number: int | str | None = None
    chunk_index: int | str | None = None
    heading_path: str = ""


@dataclass(frozen=True)
class RagResult:
    prompt: str
    sources: tuple[RagSource, ...] = ()


class RagClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def has_documents(
        self, *, user_id: uuid.UUID, project_id: uuid.UUID | None = None
    ) -> bool:
        """Cheap existence check — no embedding, search, or reranking. This
        is the "cheap deterministic signal" select_route() needs to decide
        whether RAG is even worth calling for a turn, before anything costs
        a real query or a model call.

        Scoped to this user (or project, when given) the same way
        MemoryClient.search() is — RAG/rag_pipeline gained per-user/project
        tagging 2026-09-04; a project's documents and a user's own unfiled
        uploads no longer share one global answer.
        """
        try:
            response = await self._http.get(
                f"{self._base_url}/api/status",
                params={
                    "user_id": str(user_id),
                    "project_id": str(project_id) if project_id else None,
                },
                timeout=STATUS_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("RAG status check failed, assuming yes: %s", exc)
            # Fail open: if we can't tell, don't silently skip RAG — let
            # query() and its own retry make the real determination.
            return True
        return response.json().get("points_count", 0) > 0

    async def query(
        self,
        question: str,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> RagResult | None:
        """Assembled document context for a question, scoped to this user's
        (or project's) own documents. None on any failure — same contract
        as MemoryClient.search() returning []."""
        try:
            return await with_retry(
                lambda: self._query_once(question, user_id, project_id),
                name="rag",
            )
        except Exception as exc:
            logger.warning("RAG query failed, answering without it: %s", exc)
            return None

    async def _query_once(
        self, question: str, user_id: uuid.UUID, project_id: uuid.UUID | None
    ) -> RagResult:
        response = await self._http.post(
            f"{self._base_url}/api/query",
            data={
                "question": question,
                "user_id": str(user_id),
                "project_id": str(project_id) if project_id else None,
            },
            timeout=QUERY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        return RagResult(
            prompt=body.get("assembled_prompt", ""),
            sources=tuple(
                RagSource(
                    file_name=s.get("file_name", "—"),
                    page_number=s.get("page_number"),
                    chunk_index=s.get("chunk_index"),
                    heading_path=s.get("heading_path", ""),
                )
                for s in body.get("sources", [])
            ),
        )
