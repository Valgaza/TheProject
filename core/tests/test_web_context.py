"""What the web contributes to a turn, when it's asked for, and what
happens when SearXNG can't be reached."""

from __future__ import annotations

from nexus_core.chat_service import build_prompt
from nexus_core.orchestrator import wants_web_search
from nexus_core.web_client import WebSource


class FakeWeb:
    def __init__(self, *, result: tuple[WebSource, ...] | None = None, boom: bool = False) -> None:
        self.result = result
        self.boom = boom
        self.queries: list[str] = []

    async def search(self, query: str) -> tuple[WebSource, ...] | None:
        self.queries.append(query)
        if self.boom:
            raise RuntimeError("searxng is down")
        return self.result


async def _make_chat(client) -> dict:
    project = (await client.post("/projects", json={"name": "Research"})).json()
    return (await client.post(f"/projects/{project['id']}/chats", json={})).json()


# ── The trigger phrase ───────────────────────────────────────────────────────


def test_an_ordinary_message_does_not_trigger_a_web_search():
    assert not wants_web_search("what's a knowledge graph?")


def test_search_the_internet_triggers_it():
    assert wants_web_search("Can you search the internet for the latest Python release?")


def test_search_the_web_triggers_it():
    assert wants_web_search("please search the web for this")


def test_look_it_up_online_triggers_it():
    assert wants_web_search("not sure, look it up online")
    assert wants_web_search("look this up online please")


def test_the_trigger_is_case_insensitive():
    assert wants_web_search("SEARCH THE INTERNET for cat facts")


# ── Prompt shape ──────────────────────────────────────────────────────────────


def test_web_context_sits_after_rag_before_the_conversation():
    class Row:
        def __init__(self, role, content):
            self.role, self.content = role, content

    prompt = build_prompt(
        [Row("user", "search the web for today's weather")],
        web_sources=(WebSource(title="Weather", url="https://w.example", snippet="Sunny, 22C"),),
    )

    assert [m["role"] for m in prompt] == ["system", "system", "user"]
    assert "Sunny, 22C" in prompt[1]["content"]
    assert "https://w.example" in prompt[1]["content"]


def test_no_web_sources_means_no_web_message_at_all():
    prompt = build_prompt([], web_sources=())
    assert len(prompt) == 1


# ── The turn ──────────────────────────────────────────────────────────────────


async def test_web_search_only_fires_when_the_message_asks_for_it(db_client):
    """No trigger phrase -- search() must never even be called, the same
    way RAG's query() never runs when has_documents() said no."""
    web = FakeWeb(result=(WebSource(title="T", url="https://x", snippet="s"),))
    db_client._transport.app.state.web = web
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "what is a knowledge graph?"}
    )

    assert response.status_code == 201
    assert response.json()["web_sources"] == []
    assert web.queries == []


async def test_web_context_reaches_the_model_and_the_response(db_client, provider):
    web = FakeWeb(
        result=(WebSource(title="Python 3.13 released", url="https://python.org/x", snippet="New release notes."),)
    )
    db_client._transport.app.state.web = web
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(
            f"/chats/{chat['id']}/messages",
            json={"content": "search the internet for the latest Python release"},
        )
    ).json()

    assert turn["web_sources"] == [
        {"title": "Python 3.13 released", "url": "https://python.org/x", "snippet": "New release notes."}
    ]
    assert "New release notes." in provider.chat_calls[0][1]["content"]
    assert web.queries == ["search the internet for the latest Python release"]


async def test_a_dead_searxng_does_not_fail_the_turn(db_client):
    """Degrading an answer is acceptable. Failing one is not -- same rule
    as memory and RAG."""
    db_client._transport.app.state.web = FakeWeb(boom=True)
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "search the web for this"}
    )

    assert response.status_code == 201
    assert response.json()["web_sources"] == []


async def test_no_web_client_configured_behaves_like_general_chat(db_client):
    """app.state.web unset entirely (the default) -- same as before web
    search existed, even when the message asks for one."""
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "search the internet for X"}
    )

    assert response.status_code == 201
    assert response.json()["web_sources"] == []


async def test_web_search_can_run_alongside_rag(db_client):
    """Not its own Handler -- unlike RAG, wanting a web search doesn't
    change route.handler, and both can contribute to the same turn."""
    from nexus_core.rag_client import RagResult

    class FakeRag:
        def __init__(self) -> None:
            self.has_docs = True

        async def has_documents(self, *, user_id, project_id=None) -> bool:
            return True

        async def query(self, question: str, *, user_id, project_id=None) -> RagResult:
            return RagResult(prompt="Section 3: remote work is permitted.")

    db_client._transport.app.state.rag = FakeRag()
    db_client._transport.app.state.web = FakeWeb(
        result=(WebSource(title="T", url="https://x", snippet="live result"),)
    )
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(
            f"/chats/{chat['id']}/messages",
            json={"content": "can I work remote? also search the web for company news"},
        )
    ).json()

    assert turn["route"]["handler"] == "rag"
    assert turn["web_sources"] != []
