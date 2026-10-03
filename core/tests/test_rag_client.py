"""The RAG client: what it returns, and what happens when RAG can't."""

from __future__ import annotations

import uuid

import httpx
import pytest

from nexus_core.rag_client import RagClient, RagResult, RagSource

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


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
        assert await client.has_documents(user_id=USER_ID) is True


async def test_has_documents_false_when_the_collection_is_empty():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"points_count": 0})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.has_documents(user_id=USER_ID) is False


async def test_has_documents_fails_open_when_the_status_check_errors():
    """Can't tell if there's anything ingested — don't guess "no", let the
    real query (and its own retry) make the call instead."""

    async def unreachable(request):
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(unreachable)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.has_documents(user_id=USER_ID) is True


# ── query ─────────────────────────────────────────────────────────────────────


async def test_query_parses_the_prompt_and_sources():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "assembled_prompt": "Context:\n...",
                "sources": [
                    {"file_name": "handbook.pdf", "page_number": 3, "chunk_index": 1}
                ],
            },
        )

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        result = await client.query("what does the handbook say?", user_id=USER_ID)

    assert result == RagResult(
        prompt="Context:\n...",
        sources=(RagSource(file_name="handbook.pdf", page_number=3, chunk_index=1),),
    )


async def test_query_returns_none_rather_than_raising():
    async def unreachable(request):
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(unreachable)
    async with httpx.AsyncClient(transport=transport) as http:
        client = RagClient(http, "http://rag.invalid")
        assert await client.query("anything", user_id=USER_ID) is None


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
        result = await client.query("anything", user_id=USER_ID)

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
        result = await client.query("anything", user_id=USER_ID)

    assert len(calls) == 1
    assert result is None
