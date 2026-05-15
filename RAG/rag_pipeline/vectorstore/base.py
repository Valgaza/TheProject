"""Abstract base class for vector store backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from rag_pipeline.models import Chunk, EmbeddingResult, SearchResult


class VectorStore(ABC):
    """Base interface for vector store operations.

    Supports dual-vector storage (dense + sparse) for hybrid search.
    """

    @abstractmethod
    def ensure_collection(self, dense_dim: int) -> None:
        """Create the collection if it doesn't exist.

        Args:
            dense_dim: Dimensionality of the dense vectors.
        """

    @abstractmethod
    def upsert(
        self,
        chunks: list[Chunk],
        embeddings: list[EmbeddingResult],
    ) -> int:
        """Insert or update chunks with their embeddings.

        Args:
            chunks: Chunk objects with content and metadata.
            embeddings: Corresponding embedding results (same order/length).

        Returns:
            Number of points successfully upserted.
        """

    @abstractmethod
    def hybrid_search(
        self,
        query_embedding: EmbeddingResult,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Perform hybrid search using dense + sparse vectors with RRF fusion.

        Args:
            query_embedding: The query's dense and sparse vectors.
            top_k: Number of results to return.
            filters: Optional metadata filters.

        Returns:
            List of SearchResult objects sorted by relevance.
        """

    @abstractmethod
    def get_collection_info(self) -> dict[str, Any]:
        """Return collection statistics (point count, index status, etc.)."""

    @abstractmethod
    def delete_collection(self) -> bool:
        """Delete the entire collection. Returns True if successful."""

    @abstractmethod
    def count(self) -> int:
        """Return the number of points in the collection."""
