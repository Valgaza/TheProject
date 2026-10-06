"""What memory contributes to a turn, and what happens when it can't."""

from __future__ import annotations

import httpx
import pytest

from nexus_core.chat_service import CONTEXT_DIVIDER, SYSTEM_PROMPT, build_prompt, render_memory
from nexus_core.memory_client import Fact, MemoryClient


class FakeMemory:
    def __init__(self, facts: list[Fact] | None = None, *, boom: bool = False) -> None:
        self._facts = facts or []
        self.boom = boom
        self.queries: list[str] = []

    async def search(self, query, *, user_id, project_id=None, limit=None):
        self.queries.append(query)
        if self.boom:
            raise RuntimeError("memory is down")
        return self._facts


async def _make_chat(client) -> dict:
    project = (await client.post("/projects", json={"name": "Recall"})).json()
    return (await client.post(f"/projects/{project['id']}/chats", json={})).json()


# ── Prompt shape ──────────────────────────────────────────────────────────────


def test_memory_travels_with_the_message_it_was_recalled_for():
    """Attached to the latest message, ahead of its text: the message the
    person typed is still the last thing in the window, and the turns before
    it stay a stable prefix (test_prompt_prefix.py)."""

    class Row:
        def __init__(self, role, content):
            self.role, self.content = role, content

    prompt = build_prompt(
        [Row("user", "what storage?")],
        [Fact(id="1", fact="the graph store is Neo4j", scope="project")],
    )

    assert [m["role"] for m in prompt] == ["system", "user"]
    assert prompt[0]["content"] == SYSTEM_PROMPT
    context, said = prompt[1]["content"].split(CONTEXT_DIVIDER)
    assert "Neo4j" in context
    assert said.strip() == "what storage?"


def test_no_facts_means_no_memory_message_at_all():
    """An empty 'here is what you know' block is worse than none: it invites the
    model to comment on having no memory."""
    prompt = build_prompt([], [])

    assert len(prompt) == 1


def test_recalled_facts_are_tagged_with_their_tier():
    rendered = render_memory(
        [
            Fact(id="1", fact="prefers terse answers", scope="user"),
            Fact(id="2", fact="uses Neo4j", scope="project"),
        ]
    )

    assert "[user] prefers terse answers" in rendered
    assert "[project] uses Neo4j" in rendered


def test_memory_is_framed_as_fallible():
    """Presented as ground truth, a model defends a stale fact against the user
    correcting it."""
    rendered = render_memory([Fact(id="1", fact="x", scope="user")])

    assert "out of date" in rendered
    assert "they" in rendered and "right" in rendered


# ── The turn ──────────────────────────────────────────────────────────────────


async def test_recalled_facts_reach_the_model_and_the_response(db_client, provider):
    db_client._transport.app.state.memory = FakeMemory(
        [Fact(id="f1", fact="the graph store is Neo4j", scope="project")]
    )
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": "what am I using for storage?"}
        )
    ).json()

    assert [f["fact"] for f in turn["recalled"]] == ["the graph store is Neo4j"]
    assert "Neo4j" in provider.chat_calls[0][1]["content"]


async def test_the_search_query_is_what_the_user_just_said(db_client):
    memory = FakeMemory()
    db_client._transport.app.state.memory = memory
    chat = await _make_chat(db_client)

    await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "what am I using for storage?"}
    )

    assert memory.queries == ["what am I using for storage?"]


async def test_recalled_facts_are_recorded_on_the_assistant_message(db_client):
    db_client._transport.app.state.memory = FakeMemory(
        [Fact(id="f1", fact="prefers terse answers", scope="user")]
    )
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hi"})
    ).json()

    recalled = turn["assistant_message"]["metadata"]["recalled_facts"]
    assert recalled == [
        {
            "id": "f1",
            "fact": "prefers terse answers",
            "scope": "user",
            "kind": "relationship",
        }
    ]


async def test_a_dead_memory_service_does_not_fail_the_turn(db_client):
    """Degrading an answer is acceptable. Failing one is not."""
    db_client._transport.app.state.memory = FakeMemory(boom=True)
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "still works?"}
    )

    assert response.status_code == 201
    assert response.json()["recalled"] == []


# ── The client ────────────────────────────────────────────────────────────────


async def test_the_client_returns_nothing_rather_than_raising():
    async def unreachable(request):
        raise httpx.ConnectError("no route to host")

    transport = httpx.MockTransport(unreachable)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MemoryClient(http, "http://memory.invalid")
        facts = await client.search("anything", user_id=__import__("uuid").uuid4())

    assert facts == []


async def test_the_client_returns_nothing_on_a_server_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    async with httpx.AsyncClient(transport=transport) as http:
        client = MemoryClient(http, "http://memory.invalid")
        facts = await client.search("anything", user_id=__import__("uuid").uuid4())

    assert facts == []


async def test_the_client_parses_facts():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "query": "q",
                "group_ids": ["u_x"],
                "facts": [{"id": "a", "fact": "uses Neo4j", "scope": "project"}],
            },
        )

    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MemoryClient(http, "http://memory.invalid")
        facts = await client.search("q", user_id=__import__("uuid").uuid4())

    assert facts == [Fact(id="a", fact="uses Neo4j", scope="project")]


def test_a_correction_reads_before_what_it_corrected():
    """graphiti does not always retire a superseded edge, so both versions can
    come back; dated and newest first, the model can tell which is current."""
    from datetime import datetime, timezone

    old = Fact(id="1", fact="The graph store is Neo4j.", scope="project",
               learned_at=datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc))
    new = Fact(id="2", fact="The graph store is Postgres.", scope="project",
               learned_at=datetime(2026, 10, 4, 9, 5, tzinfo=timezone.utc))
    summary = Fact(id="3", fact="Aarya builds Nexus.", scope="user", kind="entity")

    text = render_memory([old, summary, new])

    assert text.index("Postgres") < text.index("Neo4j") < text.index("Aarya builds")
    assert "learned 2026-10-04 09:05:00" in text and "the one listed first is current" in text
