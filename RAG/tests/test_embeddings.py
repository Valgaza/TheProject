"""Tests for embedding service."""

from __future__ import annotations

import pytest

from rag_pipeline.embeddings.fastembed_service import FastEmbedService


@pytest.fixture(scope="module")
def embedding_service() -> FastEmbedService:
    """Shared embedding service instance (models are slow to load)."""
    return FastEmbedService()


class TestFastEmbedService:
    """Tests for the FastEmbed embedding service."""

    def test_dense_dimension(self, embedding_service: FastEmbedService):
        """Dense model should report its dimensionality."""
        dim = embedding_service.get_dense_dimension()
        assert dim > 0
        assert dim == 384  # BGE-small-en-v1.5

    def test_embed_texts(self, embedding_service: FastEmbedService):
        """Embedding texts should produce dense + sparse vectors."""
        texts = ["Hello world", "Vector databases are fast"]
        results = embedding_service.embed_texts(texts)

        assert len(results) == 2
        for result in results:
            assert len(result.dense_vector) == 384
            assert len(result.sparse_indices) > 0
            assert len(result.sparse_indices) == len(result.sparse_values)

    def test_embed_query(self, embedding_service: FastEmbedService):
        """Query embedding should work differently than document embedding."""
        result = embedding_service.embed_query("What is RAG?")

        assert len(result.dense_vector) == 384
        assert len(result.sparse_indices) > 0
        assert result.chunk_id == "query"

    def test_embed_empty_list(self, embedding_service: FastEmbedService):
        """Embedding empty list should return empty list."""
        results = embedding_service.embed_texts([])
        assert results == []
