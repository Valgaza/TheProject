"""Tests for retrieval and reranking."""

from __future__ import annotations

import pytest

from rag_pipeline.models import SearchResult
from rag_pipeline.retrieval.reranker import CrossEncoderReranker


@pytest.fixture(scope="module")
def reranker() -> CrossEncoderReranker:
    """Shared reranker instance (model is slow to load)."""
    return CrossEncoderReranker()


def _make_search_result(content: str, score: float = 0.5) -> SearchResult:
    """Helper to create a SearchResult for testing."""
    return SearchResult(
        chunk_id="test-id",
        content=content,
        score=score,
        metadata={"file_name": "test.md"},
    )


class TestCrossEncoderReranker:
    """Tests for the cross-encoder reranker."""

    def test_rerank_basic(self, reranker: CrossEncoderReranker):
        """Reranking should re-score and reorder results."""
        results = [
            _make_search_result("Cats are great pets.", 0.8),
            _make_search_result("Machine learning uses neural networks.", 0.7),
            _make_search_result("Dogs are loyal animals.", 0.6),
        ]

        reranked = reranker.rerank(
            query="What pets are popular?",
            results=results,
            top_n=3,
        )

        assert len(reranked) > 0
        # Each result should have a rerank_score
        for r in reranked:
            assert r.rerank_score is not None

    def test_rerank_empty_results(self, reranker: CrossEncoderReranker):
        """Reranking empty results should return empty list."""
        reranked = reranker.rerank(query="test", results=[], top_n=5)
        assert reranked == []

    def test_rerank_top_n_limits(self, reranker: CrossEncoderReranker):
        """top_n should limit the number of returned results."""
        results = [_make_search_result(f"Content {i}", 0.5) for i in range(10)]
        reranked = reranker.rerank(query="test query", results=results, top_n=3)
        assert len(reranked) <= 3
