"""The RAG client: what it returns, and what happens when RAG can't."""

from __future__ import annotations

import httpx
import pytest

import uuid

from nexus_core.rag_client import RagClient, RagResult, RagSource

USER = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
PROJECT = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Every retry test in this file exercises real backoff logic through
    with_retry() — patch asyncio.sleep so exponential backoff doesn't
    actually slow the suite down. The retry *decisions* still run for real,
    only the waiting between them is skipped.
    """
    async def instant(_delay):
        return None

    monkeypatch.setattr("asyncio.sleep", instant)


# ── has_documents ─────────────────────────────────────────────────────────────


async def test_has_documents_true_when_the_collection_is_non_empty():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"points_count": 42})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.has_documents(USER, None) is True


async def test_has_documents_false_when_the_collection_is_empty():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"points_count": 0})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.has_documents(USER, None) is False


async def test_has_documents_fails_open_when_the_status_check_errors():
    """Can't tell if there's anything ingested — don't guess "no", let the
    real query (and its own retry) make the call instead."""

    async def unreachable(request):
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(unreachable)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.has_documents(USER, None) is True


async def test_has_documents_asks_only_about_the_tiers_this_question_can_read():
    """The whole point of scoping it. A project's emptiness is its own, not
    the collection's -- asking globally is what made one uploaded file drag
    every chat in every project through retrieval."""
    seen: dict[str, str] = {}

    def respond(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"points_count": 7})

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        await client.has_documents(USER, PROJECT)

    assert seen == {"user_id": str(USER), "project_id": str(PROJECT)}


# ── query ─────────────────────────────────────────────────────────────────────


async def test_query_parses_the_prompt_and_sources():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "assembled_prompt": "Context:\n...",
                "sources": [
                    {
                        "file_name": "handbook.pdf",
                        "page_number": 3,
                        "chunk_index": 1,
                        "rerank_score": 0.81,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        result = await client.query("what does the handbook say?", user_id=USER, project_id=None)

    assert result == RagResult(
        prompt="Context:\n...",
        sources=(
            RagSource(
                file_name="handbook.pdf",
                page_number=3,
                chunk_index=1,
                rerank_score=0.81,
            ),
        ),
    )
    assert result.best_score == 0.81


async def test_query_returns_none_rather_than_raising():
    async def unreachable(request):
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(unreachable)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.query("anything", user_id=USER, project_id=None) is None


async def test_query_retries_a_server_error_then_succeeds():
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(500, text="upstream is warming up")
        return httpx.Response(200, json={"assembled_prompt": "ok", "sources": []})

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        result = await client.query("anything", user_id=USER, project_id=None)

    assert len(calls) == 3
    assert result.prompt == "ok"


async def test_query_does_not_retry_a_client_error():
    """A 400 means our request was wrong — retrying the identical request
    fails the same way every time, so it should try exactly once."""
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(400, text="bad request")

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        result = await client.query("anything", user_id=USER, project_id=None)

    assert len(calls) == 1
    assert result is None


async def test_a_project_query_carries_its_project_so_the_tier_filter_can_apply():
    """Without this the request is indistinguishable from a loose one, and
    the service has no way to keep project A's corpus out of project B."""
    sent: dict[str, str] = {}

    def respond(request):
        body = request.content.decode()
        for pair in body.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                sent[k] = v
        return httpx.Response(200, json={"assembled_prompt": "", "sources": []})

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        await client.query("anything", user_id=USER, project_id=PROJECT)

    assert sent["user_id"] == str(USER)
    assert sent["project_id"] == str(PROJECT)


async def test_a_loose_query_sends_no_project_at_all():
    """Not an empty string -- absent. An empty project_id would read as a
    project whose id happens to be blank rather than as the user tier."""
    sent: dict[str, str] = {}

    def respond(request):
        for pair in request.content.decode().split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                sent[k] = v
        return httpx.Response(200, json={"assembled_prompt": "", "sources": []})

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        await client.query("anything", user_id=USER, project_id=None)

    assert "project_id" not in sent


async def test_best_score_is_zero_when_nothing_was_retrieved():
    """chat_service compares this against the floor, so it has to be a real
    number rather than raising on an empty sequence."""
    assert RagResult(prompt="", sources=()).best_score == 0.0
