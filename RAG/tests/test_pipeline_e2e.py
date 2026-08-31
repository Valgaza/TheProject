"""End-to-end integration test — full ingest + query cycle.

Uses Qdrant in-memory mode so no Docker is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.embeddings.fastembed_service import FastEmbedService
from rag_pipeline.pipelines.ingestion import IngestionPipeline
from rag_pipeline.pipelines.query import QueryPipeline
from rag_pipeline.retrieval.reranker import CrossEncoderReranker
from rag_pipeline.vectorstore.qdrant_store import QdrantStore

SAMPLE_MD = Path(__file__).parent.parent / "sample_docs" / "sample.md"


@pytest.fixture(scope="module")
def shared_services():
    """Shared services for the e2e test (expensive to initialize)."""
    embeddings = FastEmbedService()
    store = QdrantStore(use_memory=True)
    reranker = CrossEncoderReranker()
    return embeddings, store, reranker


class TestEndToEnd:
    """Full pipeline integration test."""

    def test_ingest_and_query(self, shared_services):
        """Ingest a markdown file and query it successfully."""
        embeddings, store, reranker = shared_services

        # --- Ingestion ---
        ingest_pipeline = IngestionPipeline(
            embedding_service=embeddings,
            vector_store=store,
        )
        stats = ingest_pipeline.ingest(SAMPLE_MD)

        assert stats.documents_processed > 0
        assert stats.chunks_created > 0
        assert stats.points_upserted > 0
        assert len(stats.errors) == 0

        # Verify points are in the collection
        point_count = store.count()
        assert point_count == stats.points_upserted

        # --- Query ---
        query_pipeline = QueryPipeline(
            embedding_service=embeddings,
            vector_store=store,
            reranker=reranker,
        )
        result = query_pipeline.query("What is hybrid search in RAG?")

        assert result.query == "What is hybrid search in RAG?"
        assert len(result.retrieval_results) > 0
        assert len(result.reranked_results) > 0
        assert result.assembled_context.total_chunks_used > 0
        assert result.assembled_context.prompt  # non-empty prompt
        assert result.elapsed_seconds > 0

        # The context should contain relevant information
        prompt = result.assembled_context.prompt.lower()
        assert "hybrid" in prompt or "search" in prompt or "dense" in prompt

        # Sources should be attributed
        assert len(result.assembled_context.sources) > 0
        assert result.assembled_context.sources[0]["file_name"] == "sample.md"

    def test_query_with_no_results(self, shared_services):
        """Querying for nonsensical text should still work (graceful degradation)."""
        embeddings, store, reranker = shared_services

        query_pipeline = QueryPipeline(
            embedding_service=embeddings,
            vector_store=store,
            reranker=reranker,
        )
        # Even a weird query should not crash
        result = query_pipeline.query("xyzzy plugh gibberish nonsense")
        assert result.query == "xyzzy plugh gibberish nonsense"
        # May or may not find results, but should not error
