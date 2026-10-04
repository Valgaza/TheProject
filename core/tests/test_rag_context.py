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
        self.scopes: list[tuple] = []

    async def has_documents(self, user_id, project_id) -> bool:
        return self.has_docs

    async def query(self, question: str, *, user_id, project_id) -> RagResult | None:
        self.queries.append(question)
        self.scopes.append((user_id, project_id))
        if self.boom:
            raise RuntimeError("rag is down")
        return self.result


async def _make_chat(client) -> dict:
    project = (await client.post("/projects", json={"name": "Docs"})).json()
    return (await client.post(f"/projects/{project['id']}/chats", json={})).json()


# ── Routing ───────────────────────────────────────────────────────────────────
#
# Documents are no longer a route. select_route decides which *model* runs --
# vision for an image, speech for audio -- and documents only change what the
# model is told, which a relevance floor decides per turn instead.


async def test_the_existence_of_documents_no_longer_decides_the_route():
    """The old design routed the whole turn on "does a corpus exist", which
    could only answer a question about the project and never about the
    question being asked."""
    route = await select_route(content="anything", rag=FakeRag(has_docs=True))
    assert route.handler == "general"


async def test_routing_is_the_same_with_or_without_a_rag_client():
    with_client = await select_route(content="anything", rag=FakeRag(has_docs=True))
    without = await select_route(content="anything", rag=None)
    assert with_client.handler == without.handler == "general"


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
            sources=(
                RagSource(file_name="handbook.pdf", page_number=3, rerank_score=0.9),
            ),
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
            "rerank_score": 0.9,
        }
    ]
    assert "remote work is permitted" in provider.chat_calls[0][1]["content"]


async def test_the_query_is_what_the_user_just_said(db_client):
    rag = FakeRag(
        has_docs=True,
        result=RagResult(
            prompt="context",
            sources=(RagSource(file_name="a.md", rerank_score=0.9),),
        ),
    )
    db_client._transport.app.state.rag = rag
    chat = await _make_chat(db_client)

    await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "can I work remote?"}
    )

    assert rag.queries == ["can I work remote?"]


async def test_every_turn_retrieves_now_that_documents_are_a_tier(db_client):
    """Memory is searched on every turn; documents are too. Whether either
    contributes is decided afterwards, by relevance, not beforehand by
    whether a corpus happens to exist."""
    rag = FakeRag(has_docs=False, result=None)
    db_client._transport.app.state.rag = rag
    chat = await _make_chat(db_client)

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hi"})

    assert rag.queries == ["hi"]


async def test_a_weak_match_is_retrieved_and_then_dropped(db_client, provider):
    """The floor is the whole reason documents can be a tier rather than a
    route: "thanks" in a project full of PDFs must not drag the corpus in
    front of the model."""
    db_client._transport.app.state.rag = FakeRag(
        has_docs=True,
        result=RagResult(
            prompt="Section 9: the cafeteria menu.",
            sources=(RagSource(file_name="menu.pdf", rerank_score=0.02),),
        ),
    )
    chat = await _make_chat(db_client)

    body = (
        await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": "thanks!"}
        )
    ).json()

    assert body["route"]["handler"] == "general"
    assert body["rag_sources"] == []
    assert not any("cafeteria" in m["content"] for m in provider.chat_calls[0])


async def test_the_recorded_route_says_rag_only_when_documents_were_used(db_client):
    """The label a person reads should describe what happened, not what was
    predicted before retrieval ran."""
    db_client._transport.app.state.rag = FakeRag(
        has_docs=True,
        result=RagResult(
            prompt="Section 3: remote work is permitted.",
            sources=(RagSource(file_name="handbook.pdf", rerank_score=0.88),),
        ),
    )
    chat = await _make_chat(db_client)

    body = (
        await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": "can I work remote?"}
        )
    ).json()

    assert body["route"]["handler"] == "rag"
    assert "cleared the relevance floor" in body["route"]["reason"]


async def test_a_project_chat_retrieves_with_its_project_id(db_client):
    """Without this reaching the service, no tier filter can be applied and
    every project shares one corpus."""
    rag = FakeRag(has_docs=True, result=None)
    db_client._transport.app.state.rag = rag
    chat = await _make_chat(db_client)

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hi"})

    _, project_id = rag.scopes[0]
    assert str(project_id) == chat["project_id"]


async def test_a_loose_chat_retrieves_with_no_project(db_client):
    """A chat outside every project reads the universal tier alone."""
    rag = FakeRag(has_docs=True, result=None)
    db_client._transport.app.state.rag = rag
    loose = (await db_client.post("/chats", json={})).json()

    await db_client.post(f"/chats/{loose['id']}/messages", json={"content": "hi"})

    _, project_id = rag.scopes[0]
    assert project_id is None


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
