"""Ingest has to survive the collection disappearing underneath it.

This is a regression test for a real failure. IngestionPipeline used to cache
a ``_collection_ready`` flag for the lifetime of the process, so once the
collection had been created it was never checked again. Drop the collection --
a reset, a wiped volume, someone clearing test data -- and every subsequent
upload failed with a Qdrant 404 that surfaced to the user as "couldn't be
read", forever, until somebody happened to restart the service.

Nothing about that failure pointed at its cause. The document parsed fine,
chunked fine, embedded fine, and then vanished at the last step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.embeddings.fastembed_service import FastEmbedService
from rag_pipeline.pipelines.ingestion import IngestionPipeline
from rag_pipeline.vectorstore.qdrant_store import QdrantStore

SAMPLE_MD = Path(__file__).parent.parent / "sample_docs" / "sample.md"


@pytest.fixture(scope="module")
def embeddings():
    return FastEmbedService()


def test_ingest_recreates_a_collection_that_was_dropped(embeddings):
    store = QdrantStore(use_memory=True)
    pipeline = IngestionPipeline(embedding_service=embeddings, vector_store=store)

    first = pipeline.ingest(SAMPLE_MD)
    assert first.points_upserted > 0
    assert first.errors == []

    # Someone clears the vector store while the service keeps running.
    store._client.delete_collection(store._collection_name)
    assert store.count() == 0

    second = pipeline.ingest(SAMPLE_MD)

    assert second.errors == [], (
        "ingest did not recover from a dropped collection -- the ready flag "
        "is cached again"
    )
    assert second.points_upserted > 0
    assert store.count() == second.points_upserted


def test_checking_the_collection_every_time_does_not_wipe_it(embeddings):
    """The other half of the fix.

    ensure_collection now runs on every ingest instead of once per process.
    It has to stay idempotent: if it recreated the collection it found, the
    second upload to a store would silently destroy the first, which is a
    worse bug than the one being fixed.
    """
    store = QdrantStore(use_memory=True)
    pipeline = IngestionPipeline(embedding_service=embeddings, vector_store=store)

    first = pipeline.ingest(SAMPLE_MD)
    second = pipeline.ingest(SAMPLE_MD)

    assert second.errors == []
    # Both ingests' points are present. Chunk ids are freshly generated per
    # ingest (models.py mints a uuid4 per chunk), so the same file uploaded
    # twice is two independent sets of points rather than an upsert over the
    # first. That is deliberate at the layer above: each upload is its own
    # documents row owning its own chunks, so deleting one removes exactly
    # its own and leaves the other intact.
    assert store.count() == first.points_upserted + second.points_upserted
