"""Hybrid retrieval engine — orchestrates embedding + vector search."""

from __future__ import annotations

import logging
from typing import Any

from rag_pipeline.config import settings
from rag_pipeline.embeddings.base import EmbeddingService
from rag_pipeline.models import SearchResult
from rag_pipeline.vectorstore.base import VectorStore

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Retrieval engine that combines dense and sparse search.

    Embeds the query using the same dual-vector approach used during
    ingestion, then delegates hybrid search to the vector store.

    This layer exists to decouple query embedding from search — making
    it easy to swap vector stores or add result caching without
    touching the search logic.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        top_k: int | None = None,
    ):
        """Initialize the retriever.

        Args:
            embedding_service: Service for generating query embeddings.
            vector_store: The vector store to search.
            top_k: Number of results to retrieve (default from settings).
        """
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._top_k = top_k or settings.retrieval_top_k

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Retrieve relevant chunks for a query using hybrid search.

        Args:
            query: The user's natural language query.
            top_k: Override for number of results (uses default if None).
            filters: Optional metadata filters.

        Returns:
            List of SearchResult objects ranked by RRF-fused score.
        """
        k = top_k or self._top_k
        logger.info("Retrieving top-%d results for query: '%s'", k, query[:80])

        # Step 1: Embed the query (dense + sparse)
        query_embedding = self._embedding_service.embed_query(query)

        # Step 2: Hybrid search in vector store
        results = self._vector_store.hybrid_search(
            query_embedding=query_embedding,
            top_k=k,
            filters=filters,
        )

        logger.info(
            "Retrieved %d results (top score: %.4f)",
            len(results),
            results[0].score if results else 0.0,
        )
        return results
