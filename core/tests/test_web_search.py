"""Web search: the one path where text leaves the machine.

Flag 1 in .github/IMPORTANT_BUSINESS_LOGIC_FLAGS.md is the reason these exist.
A test query like "AI news" never contains anything sensitive, so it can never
catch a leak; these use the doctor's query the flag was written about.
"""

from __future__ import annotations

import httpx
import pytest

from nexus_core.orchestrator import select_route
from nexus_core.web_client import WebClient, WebResult, WebSearch, strip_identifiers


class RewritingProvider:
    """Stands in for the rewrite model, returning whatever it is told to."""

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply

    async def chat(self, messages, *, model=None, max_tokens=None) -> str:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def searxng(seen: list[str]) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["q"])
        return httpx.Response(200, json={"results": [
            {"title": "Lymphoma treatment", "url": "https://example.org/a", "content": "..."},
        ]})
    return httpx.MockTransport(respond)


# ── The deterministic layer ──────────────────────────────────────────────────


def test_the_person_s_own_name_is_stripped_whole_or_in_parts():
    query, removed = strip_identifiers("Aarya's thesis on raft", ["Aarya Patil"])
    assert query == "thesis on raft"
    assert removed == ["Aarya's"]


def test_emails_and_long_numbers_are_stripped():
    query, _ = strip_identifiers(
        "case 2024-CV-118273 filed by x@firm.com lymphoma MRN00482913", []
    )
    assert "118273" not in query and "@" not in query and "00482913" not in query
    assert "lymphoma" in query


def test_ordinary_numbers_in_topics_survive():
    query, removed = strip_identifiers("COVID-19 guidance 2024 for GPT-4", [])
    assert query == "COVID-19 guidance 2024 for GPT-4"
    assert removed == []


# ── The client ───────────────────────────────────────────────────────────────


async def test_what_leaves_is_the_rewrite_not_the_message():
    seen: list[str] = []
    web = WebClient("http://searxng.test", transport=searxng(seen))

    result = await web.search(
        "search the internet for Priya Nair's stage 3 lymphoma options",
        provider=RewritingProvider("stage 3 lymphoma treatment options"),
        model=None,
        known_names=[],
    )

    assert seen == ["stage 3 lymphoma treatment options"]
    assert result.query_sent == "stage 3 lymphoma treatment options"
    assert result.results[0].url == "https://example.org/a"


async def test_a_rewrite_that_leaks_the_person_s_name_is_still_caught():
    seen: list[str] = []
    web = WebClient("http://searxng.test", transport=searxng(seen))

    await web.search(
        "what did I say about raft",
        provider=RewritingProvider("Aarya Patil raft consensus"),
        model=None,
        known_names=["Aarya Patil", "aarya@example.com"],
    )

    assert seen == ["raft consensus"]


@pytest.mark.parametrize("reply", [RuntimeError("model down"), "", "   "])
async def test_no_safe_query_means_nothing_is_sent(reply):
    """The raw message is never the fallback."""
    seen: list[str] = []
    web = WebClient("http://searxng.test", transport=searxng(seen))

    result = await web.search(
        "Priya Nair MRN 00482913", provider=RewritingProvider(reply), model=None, known_names=[]
    )

    assert seen == []
    assert result.query_sent is None and result.error


# ── Routing ──────────────────────────────────────────────────────────────────


async def test_the_web_only_runs_when_asked_for():
    assert (await select_route(content="what's new in raft", use_web=True)).handler == "web"
    assert (await select_route(content="search the web for raft")).handler == "web"
    assert (await select_route(content="what is raft")).handler == "general"


# ── Through a real turn ──────────────────────────────────────────────────────


class FakeWeb:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def search(self, message, *, provider, model, known_names) -> WebSearch:
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


async def test_an_unconfigured_web_search_tells_the_model_so(db_client, provider):
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "latest raft news", "use_web": True}
    )

    assert response.json()["assistant_message"]["metadata"]["web"]["error"]
    prompt_text = "\n".join(m["content"] for m in provider.chat_calls[-1])
    assert "could not check the web" in prompt_text
