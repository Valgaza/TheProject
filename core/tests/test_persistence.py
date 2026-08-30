"""Projects, chats and messages against the real schema.

These run inside a transaction that is rolled back, so they exercise the actual
constraints and triggers without disturbing the seeded rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text


async def _make_project(client, name: str = "Test project") -> dict:
    response = await client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _make_chat(client, project_id: str, title: str = "Test chat") -> dict:
    response = await client.post(f"/projects/{project_id}/chats", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_list_projects(db_client):
    created = await _make_project(db_client, "Thesis")

    assert created["name"] == "Thesis"
    assert created["domain"] == "general"

    listed = (await db_client.get("/projects")).json()
    assert created["id"] in [p["id"] for p in listed]


async def test_project_rejects_a_blank_name(db_client):
    response = await db_client.post("/projects", json={"name": ""})

    assert response.status_code == 422


async def test_chats_are_scoped_to_their_project(db_client):
    first = await _make_project(db_client, "First")
    second = await _make_project(db_client, "Second")
    chat = await _make_chat(db_client, first["id"], "Only in first")

    in_first = (await db_client.get(f"/projects/{first['id']}/chats")).json()
    in_second = (await db_client.get(f"/projects/{second['id']}/chats")).json()

    assert [c["id"] for c in in_first] == [chat["id"]]
    assert in_second == []


async def test_messages_round_trip_in_order(db_client):
    """Each post writes two rows: the question and the answer to it."""
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    for content in ("first", "second", "third"):
        response = await db_client.post(
            f"/chats/{chat['id']}/messages", json={"content": content}
        )
        assert response.status_code == 201, response.text

    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()

    assert [m["role"] for m in messages] == ["user", "assistant"] * 3
    assert [m["content"] for m in messages[::2]] == ["first", "second", "third"]


async def test_message_role_is_validated(db_client):
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "hi", "role": "wizard"}
    )

    assert response.status_code == 422


async def test_assistant_turns_cannot_be_posted(db_client):
    """The answer is generated, never supplied — otherwise a client could
    forge a transcript that memory would later ingest as fact."""
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "hi", "role": "assistant"}
    )

    assert response.status_code == 422


async def test_posting_a_message_bumps_the_chat(db_client, session_factory):
    """The trigger is what lets the recent-conversations list order by chats.

    Postgres ``now()`` is the transaction timestamp, and the whole test runs in
    one transaction, so the clock cannot be used to detect the update. Backdate
    the row instead and check the trigger moves it forward.
    """
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    backdated = datetime(2020, 1, 1, tzinfo=timezone.utc)
    async with session_factory() as session:
        await session.execute(
            text("update public.chats set updated_at = :ts where id = :id"),
            {"ts": backdated, "id": chat["id"]},
        )
        await session.commit()

    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello"})
    after = (await db_client.get(f"/chats/{chat['id']}")).json()["updated_at"]

    assert datetime.fromisoformat(after) > backdated


async def test_unknown_ids_are_404_not_500(db_client):
    missing = uuid.uuid4()

    assert (await db_client.get(f"/projects/{missing}")).status_code == 404
    assert (await db_client.get(f"/chats/{missing}")).status_code == 404
    assert (await db_client.get(f"/chats/{missing}/messages")).status_code == 404
