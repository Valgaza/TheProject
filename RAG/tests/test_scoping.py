"""Per-user/per-project scoping.

This is the regression guard for a real bug found live during demo prep: one
shared Qdrant collection meant a chat about one document could get answered
using a completely unrelated document uploaded by someone else, in a
different project. These tests exist so that specific failure mode can never
silently come back.
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

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="module")
def scoped_services():
    """The same file ingested three times under three different owners.

    Deliberately identical content for all three -- the point is that only
    the metadata/filter can tell them apart, not the text, so a passing test
    here really is testing isolation and not just "different documents
    happen to look different."
    """
    embeddings = FastEmbedService()
    store = QdrantStore(use_memory=True)
    reranker = CrossEncoderReranker()

    ingest = IngestionPipeline(embedding_service=embeddings, vector_store=store)
    ingest.ingest(SAMPLE_MD, user_id=USER_A)
    ingest.ingest(SAMPLE_MD, user_id=USER_B)
    ingest.ingest(SAMPLE_MD, user_id=USER_A, project_id=PROJECT_A)

    query = QueryPipeline(embedding_service=embeddings, vector_store=store, reranker=reranker)
    return store, query


class TestPerUserScoping:
    def test_each_users_personal_count_excludes_the_others(self, scoped_services):
        store, _ = scoped_services
        a_only = store.count(filters={"user_id": USER_A, "project_id": ""})
        b_only = store.count(filters={"user_id": USER_B, "project_id": ""})
        everything = store.count()

        assert a_only > 0
        assert b_only > 0
        # Both personal counts, plus the project-scoped upload, add up to the
        # whole collection -- nothing double-counted, nothing missing.
        project_only = store.count(filters={"project_id": PROJECT_A})
        assert a_only + b_only + project_only == everything

    def test_query_results_only_carry_the_requested_owner(self, scoped_services):
        _, query = scoped_services
        result = query.query(
            "What is hybrid search in RAG?",
            filters={"user_id": USER_A, "project_id": ""},
        )

        assert result.retrieval_results
        assert all(r.metadata.get("user_id") == USER_A for r in result.retrieval_results)
        assert all(r.metadata.get("project_id") == "" for r in result.retrieval_results)

    def test_a_users_personal_upload_never_answers_another_users_query(self, scoped_services):
        """The exact shape of the bug this guards against: User B asking a
        question should never see content tagged as User A's personal
        upload, even though both ingested the same file."""
        _, query = scoped_services
        result = query.query(
            "What is hybrid search in RAG?",
            filters={"user_id": USER_B, "project_id": ""},
        )

        assert result.retrieval_results
        assert all(r.metadata.get("user_id") != USER_A for r in result.retrieval_results)

    def test_a_projects_documents_are_scoped_by_project_alone(self, scoped_services):
        """Project scope doesn't also require a matching user_id -- a
        project already belongs to exactly one user, so the project_id
        match alone is the whole answer (see _scope_filter in rag_api.py)."""
        _, query = scoped_services
        result = query.query(
            "What is hybrid search in RAG?",
            filters={"project_id": PROJECT_A},
        )

        assert result.retrieval_results
        assert all(r.metadata.get("project_id") == PROJECT_A for r in result.retrieval_results)

    def test_an_unfiltered_query_still_sees_everything(self, scoped_services):
        """No filters at all -- the pre-scoping behavior -- still works,
        since direct/CLI use of the pipeline classes has no tenant to scope
        by and shouldn't be forced to invent one."""
        _, query = scoped_services
        result = query.query("What is hybrid search in RAG?")
        assert result.retrieval_results
