"""Isolation through the real pipeline, not just the scope arithmetic.

test_scopes.py proves read_scopes and write_scope agree with each other. This
proves the vector store honours them: the same file is ingested under several
owners, and only the scope filter can tell the copies apart. The idea, and the
bug it guards against (one shared collection answering a chat from someone
else's document), come from feature/orchestrator's test_scoping.py, rewritten
here for scope_key tiers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.embeddings.fastembed_service import FastEmbedService
from rag_pipeline.pipelines.ingestion import IngestionPipeline
from rag_pipeline.pipelines.query import QueryPipeline
from rag_pipeline.retrieval.reranker import CrossEncoderReranker
from rag_pipeline.scopes import read_scopes, write_scope
from rag_pipeline.vectorstore.qdrant_store import QdrantStore

SAMPLE_MD = Path(__file__).parent.parent / "sample_docs" / "sample.md"
QUESTION = "What is hybrid search in RAG?"

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module")
def query():
    """Identical content under four owners, so a passing test is testing the
    filter rather than documents that merely happen to look different."""
    embeddings = FastEmbedService()
    store = QdrantStore(use_memory=True)
    ingest = IngestionPipeline(embedding_service=embeddings, vector_store=store)
    for user, project in ((ALICE, None), (BOB, None), (ALICE, PROJECT_A), (ALICE, PROJECT_B)):
        ingest.ingest(
            SAMPLE_MD,
            extra_payload={"user_id": user, "project_id": project,
                           "scope_key": write_scope(user, project)},
        )
    return QueryPipeline(
        embedding_service=embeddings, vector_store=store, reranker=CrossEncoderReranker()
    )


def _scopes_seen(query: QueryPipeline, user: str, project: str | None) -> set[str]:
    result = query.query(QUESTION, filters={"scope_key": read_scopes(user, project)})
    assert result.retrieval_results, "the filter should still let something through"
    return {r.metadata["scope_key"] for r in result.retrieval_results}


def test_a_loose_question_sees_only_its_own_universal_tier(query):
    assert _scopes_seen(query, ALICE, None) == {write_scope(ALICE, None)}


def test_another_person_never_sees_alice_s_documents(query):
    assert _scopes_seen(query, BOB, None) == {write_scope(BOB, None)}


def test_a_project_question_sees_its_project_and_the_universal_tier(query):
    seen = _scopes_seen(query, ALICE, PROJECT_A)
    assert write_scope(ALICE, PROJECT_B) not in seen
    assert seen <= {write_scope(ALICE, None), write_scope(ALICE, PROJECT_A)}


def test_counts_follow_the_same_rule(query):
    loose = query.count_in_scopes(read_scopes(ALICE, None))
    in_project = query.count_in_scopes(read_scopes(ALICE, PROJECT_A))
    assert loose > 0
    # The project adds its own copy on top of the universal one, nothing more.
    assert in_project == 2 * loose
