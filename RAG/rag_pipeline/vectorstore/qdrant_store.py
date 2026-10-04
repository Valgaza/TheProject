"""Qdrant vector store implementation with hybrid search support.

Supports both Docker-based Qdrant server and in-memory mode for testing.
Uses Qdrant's Query API for server-side RRF fusion of dense + sparse results.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient, models

from rag_pipeline.config import settings
from rag_pipeline.models import Chunk, EmbeddingResult, SearchResult
from rag_pipeline.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


class QdrantStore(VectorStore):
    """Qdrant vector store with hybrid search (dense + sparse vectors).

    Features:
        - Dual vector storage (dense for semantics, sparse for keywords)
        - Server-side RRF fusion via Qdrant's Query API
        - Idempotent collection creation
        - Batch upsert with configurable batch size
        - Rich metadata filtering
        - Supports Docker server OR in-memory mode
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection_name: str | None = None,
        use_memory: bool | None = None,
        batch_size: int = 100,
    ):
        self._collection_name = collection_name or settings.qdrant_collection_name
        self._batch_size = batch_size

        use_mem = use_memory if use_memory is not None else settings.qdrant_use_memory

        if use_mem:
            logger.info("Connecting to Qdrant in-memory mode")
            self._client = QdrantClient(location=":memory:")
        else:
            _host = host or settings.qdrant_host
            _port = port or settings.qdrant_port
            logger.info("Connecting to Qdrant at %s:%d", _host, _port)
            self._client = QdrantClient(host=_host, port=_port)

    @property
    def client(self) -> QdrantClient:
        """Access the underlying Qdrant client."""
        return self._client

    def ensure_collection(self, dense_dim: int) -> None:
        """Create the collection with dense + sparse vector configs.

        Idempotent — skips creation if the collection already exists.

        Args:
            dense_dim: Dimensionality of the dense embedding model.
        """
        collections = self._client.get_collections().collections
        existing_names = {c.name for c in collections}

        if self._collection_name in existing_names:
            logger.info("Collection '%s' already exists", self._collection_name)
            return

        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=dense_dim,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,  # BM25-style IDF weighting
                ),
            },
        )
        logger.info(
            "Created collection '%s' (dense_dim=%d)",
            self._collection_name, dense_dim,
        )

    def upsert(
        self,
        chunks: list[Chunk],
        embeddings: list[EmbeddingResult],
    ) -> int:
        """Batch upsert chunks with dense + sparse vectors and metadata.

        Args:
            chunks: Chunk objects (content + metadata).
            embeddings: Corresponding EmbeddingResult objects.

        Returns:
            Total number of points upserted.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) must be same length"
            )
        if not chunks:
            return 0

        total_upserted = 0

        # Process in batches
        for batch_start in range(0, len(chunks), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(chunks))
            batch_chunks = chunks[batch_start:batch_end]
            batch_embeddings = embeddings[batch_start:batch_end]

            points = []
            for chunk, emb in zip(batch_chunks, batch_embeddings):
                # Build payload from chunk metadata
                payload = {
                    "content": chunk.content,
                    "document_id": chunk.metadata.document_id,
                    "source_path": chunk.metadata.source_path,
                    "document_type": chunk.metadata.document_type.value,
                    "file_name": chunk.metadata.file_name,
                    "chunk_index": chunk.metadata.chunk_index,
                    "total_chunks": chunk.metadata.total_chunks,
                    "heading_path": chunk.metadata.heading_path,
                    "char_count": chunk.metadata.char_count,
                }
                if chunk.metadata.page_number is not None:
                    payload["page_number"] = chunk.metadata.page_number

                points.append(models.PointStruct(
                    id=chunk.id,
                    vector={
                        DENSE_VECTOR_NAME: emb.dense_vector,
                        SPARSE_VECTOR_NAME: models.SparseVector(
                            indices=emb.sparse_indices,
                            values=emb.sparse_values,
                        ),
                    },
                    payload=payload,
                ))

            self._client.upsert(
                collection_name=self._collection_name,
                points=points,
            )
            total_upserted += len(points)
            logger.debug(
                "Upserted batch %d-%d (%d points)",
                batch_start, batch_end, len(points),
            )

        logger.info("Total upserted: %d points", total_upserted)
        return total_upserted

    def hybrid_search(
        self,
        query_embedding: EmbeddingResult,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Perform hybrid search with server-side RRF fusion.

        Sends both dense and sparse queries to Qdrant, which fuses them
        using Reciprocal Rank Fusion for optimal result ordering.

        Args:
            query_embedding: Query's dense + sparse vectors.
            top_k: Number of results to return.
            filters: Optional metadata filters (e.g., {"file_name": "report.pdf"}).

        Returns:
            SearchResult objects sorted by fused relevance score.
        """
        # Build Qdrant filter if provided
        qdrant_filter = self._build_filter(filters) if filters else None

        # Use Qdrant's Query API with prefetch for hybrid search
        results = self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[
                models.Prefetch(
                    query=query_embedding.dense_vector,
                    using=DENSE_VECTOR_NAME,
                    limit=top_k * 2,  # over-fetch for better fusion
                    filter=qdrant_filter,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=query_embedding.sparse_indices,
                        values=query_embedding.sparse_values,
                    ),
                    using=SPARSE_VECTOR_NAME,
                    limit=top_k * 2,
                    filter=qdrant_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
        ).points

        search_results: list[SearchResult] = []
        for point in results:
            payload = point.payload or {}
            search_results.append(SearchResult(
                chunk_id=str(point.id),
                content=payload.get("content", ""),
                score=point.score if point.score is not None else 0.0,
                metadata={
                    k: v for k, v in payload.items()
                    if k != "content"
                },
            ))

        logger.info("Hybrid search returned %d results", len(search_results))
        return search_results

    def get_collection_info(self) -> dict[str, Any]:
        """Return collection statistics."""
        try:
            info = self._client.get_collection(self._collection_name)
            return {
                "name": self._collection_name,
                "points_count": info.points_count,
                "vectors_count": getattr(info, "vectors_count", "n/a"),
                "indexed_vectors_count": getattr(info, "indexed_vectors_count", "n/a"),
                "status": info.status.value if info.status else "unknown",
                "optimizer_status": str(info.optimizer_status),
            }
        except Exception as e:
            return {"name": self._collection_name, "error": str(e)}

    def delete_collection(self) -> bool:
        """Delete the collection entirely."""
        try:
            self._client.delete_collection(self._collection_name)
            logger.info("Deleted collection '%s'", self._collection_name)
            return True
        except Exception as e:
            logger.error("Failed to delete collection: %s", e)
            return False

    def count(self) -> int:
        """Return the number of points in the collection."""
        try:
            info = self._client.get_collection(self._collection_name)
            return info.points_count or 0
        except Exception:
            return 0

    @staticmethod
    def _build_filter(filters: dict[str, Any]) -> models.Filter:
        """Convert a simple dict of filters to Qdrant's Filter model.

        Supports:
            - Exact match for strings and ints
            - List values become 'any of' conditions
        """
        conditions: list[models.Condition] = []
        for key, value in filters.items():
            if isinstance(value, list):
                conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchAny(any=value),
                    )
                )
            else:
                conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                )
        return models.Filter(must=conditions)
