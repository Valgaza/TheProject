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
# Parsing a PDF, chunking it, embedding every chunk and upserting the batch.
# Nobody is watching a spinner mid-turn for this one — it is its own request.
INGEST_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class RagSource:
    file_name: str
    page_number: int | str | None = None
    chunk_index: int | str | None = None
    heading_path: str = ""
    # The cross-encoder's score for this chunk against the question. Carried
    # up because the decision "is this worth putting in front of the model"
    # is core's to make, the same way memory's cosine floor is.
    rerank_score: float = 0.0


@dataclass(frozen=True)
class RagResult:
    prompt: str
    sources: tuple[RagSource, ...] = ()
    # How long the RAG pipeline itself took, as it reports it. None when the
    # service doesn't say (an older build, or a test double).
    elapsed_seconds: float | None = None

    @property
    def best_score(self) -> float:
        """The strongest match retrieved, or 0.0 if nothing was."""
        return max((s.rerank_score for s in self.sources), default=0.0)


class RagClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def has_documents(
        self, user_id: uuid.UUID, project_id: uuid.UUID | None
    ) -> bool:
        """Cheap existence check — no embedding, search, or reranking. This
        is the "cheap deterministic signal" select_route() needs to decide
        whether RAG is even worth calling for a turn, before anything costs
        a real query or a model call.

        Scoped since 2026-09-03: this asks whether documents exist in the
        tiers *this* question may read, not whether any exist anywhere. The
        unscoped version meant one uploaded file sent every turn of every
        chat through retrieval, including a project whose corpus was empty.
        """
        try:
            params: dict[str, str] = {"user_id": str(user_id)}
            if project_id is not None:
                params["project_id"] = str(project_id)
            response = await self._http.get(
                f"{self._base_url}/api/status",
                params=params,
                timeout=STATUS_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("RAG status check failed, assuming yes: %s", exc)
            # Fail open: if we can't tell, don't silently skip RAG — let
            # query() and its own retry make the real determination.
            return True
        return response.json().get("points_count", 0) > 0

    async def ingest(
        self,
        *,
        file_name: str,
        content: bytes,
        content_type: str | None,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None,
        document_id: uuid.UUID,
    ) -> dict:
        """Push one file into the tier it belongs to.

        Unlike query(), this raises. A failed retrieval degrades an answer and
        should be swallowed; a failed upload is the whole operation the person
        asked for, and telling them it worked would be a lie they only
        discover when the file answers nothing.
        """
        data = {
            "user_id": str(user_id),
            "document_id": str(document_id),
        }
        if project_id is not None:
            data["project_id"] = str(project_id)
        response = await self._http.post(
            f"{self._base_url}/api/ingest",
            data=data,
            files={"file": (file_name, content, content_type or "application/octet-stream")},
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    async def excerpt(self, document_id: uuid.UUID, chars: int = 4000) -> str:
        """The opening of a document, already parsed and cleaned.

        Returns "" on any failure. The caller catalogues documents into the
        graph, and a graph entry that never appears is a smaller problem than
        an upload that errors after the file is safely stored.
        """
        try:
            response = await self._http.get(
                f"{self._base_url}/api/documents/{document_id}/excerpt",
                params={"chars": chars},
                timeout=QUERY_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json().get("excerpt", "")
        except Exception as exc:
            logger.warning("Excerpt unavailable for %s: %s", document_id, exc)
            return ""

    async def delete_document(self, document_id: uuid.UUID) -> int:
        """Remove a document's chunks. Returns how many were removed.

        Failing soft here on purpose: the Postgres row is the record a person
        sees, so deletion should not be blocked by the vector store being
        briefly unreachable. Orphaned chunks are recoverable; a file that
        refuses to disappear from a list is not something a user can fix.
        """
        try:
            response = await self._http.delete(
                f"{self._base_url}/api/documents/{document_id}",
                timeout=STATUS_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return int(response.json().get("chunks_removed", 0))
        except Exception as exc:
            logger.warning("RAG delete failed for %s, chunks orphaned: %s", document_id, exc)
            return 0

    async def query(
        self,
        question: str,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None,
    ) -> RagResult | None:
        """Assembled document context for a question. None on any failure —
        same contract as MemoryClient.search() returning []."""
        try:
            return await with_retry(
                lambda: self._query_once(question, user_id, project_id),
                name="rag",
            )
        except Exception as exc:
            logger.warning("RAG query failed, answering without it: %s", exc)
            return None

    async def _query_once(
        self,
        question: str,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None,
    ) -> RagResult:
        response = await self._http.post(
            f"{self._base_url}/api/query",
            data={
                "question": question,
                "user_id": str(user_id),
                **({"project_id": str(project_id)} if project_id else {}),
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
                    rerank_score=float(s.get("rerank_score") or 0.0),
                )
                for s in body.get("sources", [])
            ),
            elapsed_seconds=(
                float(body["elapsed_seconds"]) if body.get("elapsed_seconds") is not None else None
            ),
        )
