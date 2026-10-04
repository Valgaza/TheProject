"""FastEmbed-based embedding service for dense + sparse vectors.

Uses ONNX Runtime under the hood — runs entirely on CPU, no GPU required.
The models are automatically downloaded and cached on first use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rag_pipeline.config import settings
from rag_pipeline.embeddings.base import EmbeddingService
from rag_pipeline.models import EmbeddingResult

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding, TextEmbedding

logger = logging.getLogger(__name__)


class FastEmbedService(EmbeddingService):
    """Embedding service using FastEmbed (bundled with qdrant-client).

    Generates both dense and sparse embeddings for hybrid search:
      - Dense:  BAAI/bge-small-en-v1.5 (384 dims) — semantic similarity
      - Sparse: Qdrant/bm25                       — keyword matching

    Models are lazy-loaded on first use to avoid slow import times.
    """

    def __init__(
        self,
        dense_model_name: str | None = None,
        sparse_model_name: str | None = None,
        batch_size: int | None = None,
    ):
        self._dense_model_name = dense_model_name or settings.dense_embedding_model
        self._sparse_model_name = sparse_model_name or settings.sparse_embedding_model
        self._batch_size = batch_size or settings.embedding_batch_size

        # Lazy-loaded model instances
        self._dense_model: TextEmbedding | None = None
        self._sparse_model: SparseTextEmbedding | None = None
        self._dense_dim: int | None = None

    @property
    def dense_model(self) -> TextEmbedding:
        """Lazy-load the dense embedding model."""
        if self._dense_model is None:
            from fastembed import TextEmbedding

            logger.info("Loading dense embedding model: %s", self._dense_model_name)
            self._dense_model = TextEmbedding(model_name=self._dense_model_name)
        return self._dense_model

    @property
    def sparse_model(self) -> SparseTextEmbedding:
        """Lazy-load the sparse embedding model."""
        if self._sparse_model is None:
            from fastembed import SparseTextEmbedding

            logger.info("Loading sparse embedding model: %s", self._sparse_model_name)
            self._sparse_model = SparseTextEmbedding(model_name=self._sparse_model_name)
        return self._sparse_model

    def get_dense_dimension(self) -> int:
        """Return the dimensionality of the dense model.

        Determined by encoding a single test string on first call.
        """
        if self._dense_dim is None:
            test_embeddings = list(self.dense_model.embed(["test"]))
            self._dense_dim = len(test_embeddings[0])
            logger.info("Dense embedding dimension: %d", self._dense_dim)
        return self._dense_dim

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate dense + sparse embeddings for document chunks.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of EmbeddingResult with dense and sparse vectors.
        """
        if not texts:
            return []

        logger.info("Embedding %d texts (batch_size=%d)", len(texts), self._batch_size)

        # Generate dense embeddings
        dense_vectors = list(self.dense_model.embed(
            texts,
            batch_size=self._batch_size,
        ))

        # Generate sparse embeddings
        sparse_vectors = list(self.sparse_model.embed(
            texts,
            batch_size=self._batch_size,
        ))

        results: list[EmbeddingResult] = []
        for i, (dense, sparse) in enumerate(zip(dense_vectors, sparse_vectors)):
            results.append(EmbeddingResult(
                chunk_id="",  # Will be filled by the caller
                dense_vector=dense.tolist(),
                sparse_indices=sparse.indices.tolist(),
                sparse_values=sparse.values.tolist(),
            ))

        logger.info("Generated %d embedding pairs", len(results))
        return results

    def embed_query(self, query: str) -> EmbeddingResult:
        """Generate embeddings for a single query.

        Uses the same models but with query-specific encoding
        (FastEmbed handles the query prefix internally for BGE models).

        Args:
            query: The search query.

        Returns:
            EmbeddingResult with dense and sparse vectors.
        """
        dense_vectors = list(self.dense_model.query_embed(query))
        sparse_vectors = list(self.sparse_model.query_embed(query))

        dense = dense_vectors[0]
        sparse = sparse_vectors[0]

        return EmbeddingResult(
            chunk_id="query",
            dense_vector=dense.tolist(),
            sparse_indices=sparse.indices.tolist(),
            sparse_values=sparse.values.tolist(),
        )
