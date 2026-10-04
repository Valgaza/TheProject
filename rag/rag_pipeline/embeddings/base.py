"""Abstract base class for embedding services."""

from __future__ import annotations

from abc import ABC, abstractmethod

from rag_pipeline.models import EmbeddingResult


class EmbeddingService(ABC):
    """Base interface for embedding generation.

    Implementations produce both dense and sparse vectors for each text,
    enabling hybrid search in the vector store.
    """

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate dense + sparse embeddings for a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of EmbeddingResult objects with dense and sparse vectors.
        """

    @abstractmethod
    def embed_query(self, query: str) -> EmbeddingResult:
        """Generate embeddings for a single query string.

        Some models use different prefixes/modes for queries vs. documents.

        Args:
            query: The query string to embed.

        Returns:
            EmbeddingResult with dense and sparse vectors.
        """

    @abstractmethod
    def get_dense_dimension(self) -> int:
        """Return the dimensionality of the dense embedding model."""
