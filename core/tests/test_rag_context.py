"""What RAG contributes to a turn, and what happens when it can't."""

from __future__ import annotations

import pytest

from nexus_core.chat_service import build_prompt, generate_turn
from nexus_core.orchestrator import select_route
from nexus_core.rag_client import RagResult, RagSource


class FakeRag:
    def __init__(
        self,
        *,
        has_docs: bool = False,
        result: RagResult | None = None,
        boom: bool = False,
    ) -> None:
        self.has_docs = has_docs
        self.result = result
        self.boom = boom
        self.queries: list[str] = []

    async def has_documents(self) -> bool:
        return self.has_docs

    async def query(self, question: str) -> RagResult | None:
        self.queries.append(question)
        if self.boom:
            raise RuntimeError("rag is down")
        return self.result


async def _make_chat(client) -> dict:
    project = (await client.post("/projects", json={"name": "Docs"})).json()
    return (await client.post(f"/projects/{project['id']}/chats", json={})).json()


# ── Routing ───────────────────────────────────────────────────────────────────


async def test_routes_to_rag_when_the_project_has_documents():
    route = await select_route(content="anything", rag=FakeRag(has_docs=True))
    assert route.handler == "rag"


async def test_routes_to_general_when_rag_is_empty():
    route = await select_route(content="anything", rag=FakeRag(has_docs=False))
    assert route.handler == "general"
    assert "no documents" in route.reason


async def test_routes_to_general_when_no_rag_client_is_configured():
    route = await select_route(content="anything", rag=None)
    assert route.handler == "general"
    assert "no RAG client" in route.reason


# ── Prompt shape ──────────────────────────────────────────────────────────────


def test_rag_context_sits_between_memory_and_the_conversation():
    class Row:
        def __init__(self, role, content):
            self.role, self.content = role, content

    prompt = build_prompt(
        [Row("user", "what does the handbook say?")],
        rag_result=RagResult(prompt="Section 3: remote work is permitted."),
    )

    assert [m["role"] for m in prompt] == ["system", "system", "user"]
    assert "remote work is permitted" in prompt[1]["content"]


def test_no_rag_result_means_no_rag_message_at_all():
    prompt = build_prompt([], rag_result=None)
    assert len(prompt) == 1


def test_an_empty_rag_prompt_is_treated_the_same_as_no_result():
    """RagResult(prompt="") happens when the route said "rag" but the actual
    query came back with nothing usable — should not inject an empty block."""
    prompt = build_prompt([], rag_result=RagResult(prompt=""))
    assert len(prompt) == 1


# ── The turn ──────────────────────────────────────────────────────────────────


async def test_rag_context_reaches_the_model_and_the_response(db_client, provider):
    db_client._transport.app.state.rag = FakeRag(
        has_docs=True,
        result=RagResult(
            prompt="Section 3: remote work is permitted.",
            sources=(RagSource(file_name="handbook.pdf", page_number=3),),
        ),
    )
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": "can I work remote?"}
        )
    ).json()

    assert turn["route"]["handler"] == "rag"
    assert turn["rag_sources"] == [
        {
            "file_name": "handbook.pdf",
            "page_number": 3,
            "chunk_index": None,
            "heading_path": "",
        }
    ]
    assert "remote work is permitted" in provider.chat_calls[0][1]["content"]


async def test_the_query_is_what_the_user_just_said(db_client):
    rag = FakeRag(has_docs=True, result=RagResult(prompt="context"))
    db_client._transport.app.state.rag = rag
    chat = await _make_chat(db_client)

    await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "can I work remote?"}
    )

    assert rag.queries == ["can I work remote?"]


async def test_rag_is_never_queried_when_the_route_is_general(db_client):
    """has_documents() said no — query() should never even be called."""
    rag = FakeRag(has_docs=False)
    db_client._transport.app.state.rag = rag
    chat = await _make_chat(db_client)

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hi"})

    assert rag.queries == []


async def test_a_dead_rag_service_does_not_fail_the_turn(db_client):
    """Degrading an answer is acceptable. Failing one is not — same rule as
    memory."""
    db_client._transport.app.state.rag = FakeRag(has_docs=True, boom=True)
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "still works?"}
    )

    assert response.status_code == 201
    assert response.json()["rag_sources"] == []


async def test_no_rag_client_configured_behaves_like_general_chat(db_client):
    """app.state.rag unset entirely (the default) — same as before RAG existed."""
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "hi"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["route"]["handler"] == "general"
    assert body["rag_sources"] == []
