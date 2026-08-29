"""Generation: prompts, routing, titles, and what happens when the model fails."""

from __future__ import annotations

import pytest

from app.chat_service import (
    SYSTEM_PROMPT,
    TITLE_MAX_CHARS,
    _derive_title,
    build_prompt,
)
from app.orchestrator import select_route


async def _make_chat(client, title: str = "New conversation") -> dict:
    project = (await client.post("/projects", json={"name": "Turns"})).json()
    response = await client.post(f"/projects/{project['id']}/chats", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def test_route_is_general_and_explains_itself():
    route = select_route(content="anything at all")

    assert route.handler == "general"
    assert route.reason


def test_prompt_starts_with_the_system_message_and_drops_system_rows():
    class Row:
        def __init__(self, role, content):
            self.role, self.content = role, content

    prompt = build_prompt(
        [Row("user", "hello"), Row("system", "ignore me"), Row("assistant", "hi")]
    )

    assert prompt[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert prompt[1:] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


async def test_posting_a_message_returns_an_answer(db_client, provider):
    chat = await _make_chat(db_client)

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "what is a knowledge graph?"}
    )

    assert response.status_code == 201, response.text
    turn = response.json()
    assert turn["user_message"]["content"] == "what is a knowledge graph?"
    assert turn["assistant_message"]["content"] == "fake reply"
    assert turn["assistant_message"]["role"] == "assistant"
    assert turn["route"]["handler"] == "general"
    assert len(provider.chat_calls) == 1


async def test_the_prompt_carries_prior_turns(db_client, provider):
    """Multi-turn memory within a chat — the thing that makes it a conversation."""
    chat = await _make_chat(db_client)

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "first"})
    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "second"})

    second_prompt = provider.chat_calls[1]
    assert [m["content"] for m in second_prompt] == [
        SYSTEM_PROMPT,
        "first",
        "fake reply",
        "second",
    ]


async def test_chats_do_not_leak_into_each_other(db_client, provider):
    project = (await db_client.post("/projects", json={"name": "Two chats"})).json()
    a = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()
    b = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()

    await db_client.post(f"/chats/{a['id']}/messages", json={"content": "only in A"})
    await db_client.post(f"/chats/{b['id']}/messages", json={"content": "only in B"})

    prompt_for_b = provider.chat_calls[1]
    assert "only in A" not in [m["content"] for m in prompt_for_b]


async def test_the_first_message_titles_the_chat(db_client):
    chat = await _make_chat(db_client)

    turn = (
        await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": "Compare vector metrics"}
        )
    ).json()

    assert turn["chat_title"] == "Compare vector metrics"
    assert (await db_client.get(f"/chats/{chat['id']}")).json()["title"] == (
        "Compare vector metrics"
    )


async def test_a_deliberate_title_survives_the_first_message(db_client):
    chat = await _make_chat(db_client, title="Named by hand")

    turn = (
        await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello"})
    ).json()

    assert turn["chat_title"] == "Named by hand"


async def test_a_provider_failure_still_records_both_turns(db_client, provider):
    """A dead API key must not lose the user's message."""
    chat = await _make_chat(db_client)

    async def boom(messages, **kwargs):
        raise RuntimeError("upstream is down")

    provider.chat = boom

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "does this survive?"}
    )

    assert response.status_code == 201, response.text
    assert "error" in response.json()["assistant_message"]["metadata"]

    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "does this survive?"


@pytest.mark.parametrize(
    "content, expected",
    [
        ("Short one", "Short one"),
        ("first line\nsecond line", "first line"),
        ("   padded   ", "padded"),
    ],
)
def test_title_derivation(content, expected):
    assert _derive_title(content) == expected


def test_a_long_title_is_clipped_on_a_word_boundary():
    title = _derive_title("word " * 40)

    assert len(title) <= TITLE_MAX_CHARS + 1  # room for the ellipsis
    assert title.endswith("…")
    assert set(title.rstrip("…").split()) == {"word"}
