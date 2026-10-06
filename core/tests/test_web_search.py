"""Web search: the one path where text leaves the machine.

Flag 1 in docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md is the reason these exist.
A test query like "AI news" never contains anything sensitive, so it can never
catch a leak; these use the doctor's query the flag was written about.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nexus_core.chat_service import build_prompt
from nexus_core.orchestrator import select_route
from nexus_core.rag_client import RagResult, RagSource
from nexus_core.web_client import WebClient, WebResult, WebSearch


# ── The client (redaction itself is tested in websearch/tests) ───────────────


def websearch(seen: list[dict], status: int = 200) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(status, json={
            "query_sent": "stage 3 lymphoma treatment options",
            "results": [{"title": "Lymphoma", "url": "https://example.org/a", "snippet": "..."}],
            "stripped": ["00482913"], "blocked": False, "error": None,
        })
    return httpx.MockTransport(respond)


async def test_core_sends_the_message_and_the_names_to_redact():
    seen: list[dict] = []
    web = WebClient(httpx.AsyncClient(transport=websearch(seen)), "http://websearch.test")

    result = await web.search("Priya's lymphoma, MRN 00482913", known_names=["Aarya Patil"], user_id="u1")

    assert seen == [{"message": "Priya's lymphoma, MRN 00482913", "known_names": ["Aarya Patil"],
                     "user_id": "u1"}]
    assert result.query_sent == "stage 3 lymphoma treatment options"
    assert result.results[0].url == "https://example.org/a"
    assert result.stripped == ("00482913",)


async def test_an_unreachable_service_is_an_error_not_an_exception():
    def refuse(request):
        raise httpx.ConnectError("down", request=request)

    web = WebClient(httpx.AsyncClient(transport=httpx.MockTransport(refuse)), "http://websearch.test")
    result = await web.search("raft news", known_names=[])
    assert result.query_sent is None and "unreachable" in result.error


# ── Routing ──────────────────────────────────────────────────────────────────


async def test_the_web_only_runs_when_asked_for():
    assert (await select_route(content="what's new in raft", use_web=True)).handler == "web"
    assert (await select_route(content="search the web for raft")).handler == "web"
    assert (await select_route(content="what is raft")).handler == "general"


@pytest.mark.parametrize("content", [
    "search the internet for raft",
    "Search The Web for raft",
    "can you look it up online?",
    "look this up online please",
])
async def test_every_trigger_phrase_routes_to_the_web(content):
    assert (await select_route(content=content)).handler == "web"


# ── The prompt ───────────────────────────────────────────────────────────────


class Row:
    def __init__(self, role: str, content: str) -> None:
        self.role, self.content = role, content


def test_web_results_sit_after_documents_and_before_the_conversation():
    prompt = build_prompt(
        [Row("user", "can I work remote? also search the web for news")],
        rag_result=RagResult(prompt="Section 3: remote work is permitted."),
        web=WebSearch(
            query_sent="company news",
            results=(WebResult(title="News", url="https://n.example", snippet="Sunny"),),
        ),
    )

    assert [m["role"] for m in prompt] == ["system", "system", "system", "user"]
    assert "remote work is permitted" in prompt[1]["content"]
    assert "https://n.example" in prompt[2]["content"]


def test_no_web_search_means_no_web_message():
    assert len(build_prompt([Row("user", "hi")])) == 2


# ── Through a real turn ──────────────────────────────────────────────────────


class FakeWeb:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def search(self, message, *, known_names, user_id=None) -> WebSearch:
        self.messages.append(message)
        return WebSearch(
            query_sent="raft consensus",
            results=(WebResult(title="Raft", url="https://raft.github.io", snippet="..."),),
        )


async def _make_chat(db_client) -> dict:
    return (await db_client.post("/chats", json={"title": "Web"})).json()


async def test_a_web_turn_records_exactly_what_was_sent(db_client, provider):
    db_client._transport.app.state.web = FakeWeb()
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "what's raft", "use_web": True}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route"]["handler"] == "web"
    assert body["assistant_message"]["metadata"]["web"]["query_sent"] == "raft consensus"
    prompt_text = "\n".join(m["content"] for m in provider.chat_calls[-1])
    assert "https://raft.github.io" in prompt_text


async def test_without_the_button_nothing_goes_to_the_web(db_client):
    web = FakeWeb()
    db_client._transport.app.state.web = web
    chat = await _make_chat(db_client)

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "what's raft"})

    assert web.messages == []


class FakeRag:
    async def query(self, question, *, user_id, project_id) -> RagResult:
        return RagResult(
            prompt="Section 3: remote work is permitted.",
            sources=(RagSource(file_name="handbook.pdf", rerank_score=0.9),),
        )


async def test_documents_and_the_web_can_inform_one_turn(db_client, provider):
    """The web is a route and documents are a tier, so both can contribute.
    The label stays "web": it is the one that says words left the machine."""
    db_client._transport.app.state.rag = FakeRag()
    db_client._transport.app.state.web = FakeWeb()
    chat = await _make_chat(db_client)

    body = (await db_client.post(
        f"/chats/{chat['id']}/messages",
        json={"content": "can I work remote? and what's raft", "use_web": True},
    )).json()

    assert body["route"]["handler"] == "web"
    assert body["rag_sources"]
    prompt_text = "\n".join(m["content"] for m in provider.chat_calls[-1])
    assert "remote work is permitted" in prompt_text
    assert "https://raft.github.io" in prompt_text


async def test_a_web_client_that_raises_still_answers(db_client, provider):
    class ExplodingWeb:
        async def search(self, message, *, known_names, user_id=None) -> WebSearch:
            raise RuntimeError("searxng is down")

    db_client._transport.app.state.web = ExplodingWeb()
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "latest raft news", "use_web": True}
    )

    assert response.status_code == 201, response.text
    assert response.json()["assistant_message"]["metadata"]["web"]["error"]
    prompt_text = "\n".join(m["content"] for m in provider.chat_calls[-1])
    assert "could not check the web" in prompt_text


async def test_an_unconfigured_web_search_tells_the_model_so(db_client, provider):
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "latest raft news", "use_web": True}
    )

    assert response.json()["assistant_message"]["metadata"]["web"]["error"]
    prompt_text = "\n".join(m["content"] for m in provider.chat_calls[-1])
    assert "could not check the web" in prompt_text
