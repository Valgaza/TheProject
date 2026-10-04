"""Rename, pin/unpin, move to project, and delete -- the sidebar's
three-dot menu."""

from __future__ import annotations


async def _make_project(client, name: str = "Home") -> dict:
    response = await client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _make_chat(client, project_id: str, title: str = "New conversation") -> dict:
    response = await client.post(f"/projects/{project_id}/chats", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


async def test_rename_updates_the_title(db_client):
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    response = await db_client.patch(f"/chats/{chat['id']}", json={"title": "Renamed"})

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"
    assert (await db_client.get(f"/chats/{chat['id']}")).json()["title"] == "Renamed"


async def test_pin_and_unpin(db_client):
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])
    assert chat["pinned"] is False

    pinned = (
        await db_client.patch(f"/chats/{chat['id']}", json={"pinned": True})
    ).json()
    assert pinned["pinned"] is True

    unpinned = (
        await db_client.patch(f"/chats/{chat['id']}", json={"pinned": False})
    ).json()
    assert unpinned["pinned"] is False


async def test_move_to_another_owned_project(db_client):
    a = await _make_project(db_client, "A")
    b = await _make_project(db_client, "B")
    chat = await _make_chat(db_client, a["id"])

    response = await db_client.patch(f"/chats/{chat['id']}", json={"project_id": b["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == b["id"]


async def test_cannot_move_a_chat_into_someone_elses_project(db_client, session_factory):
    """A project id that exists but belongs to a different user must 404,
    the same as a project id that doesn't exist at all -- neither should
    reveal which is true."""
    import uuid

    from nexus_core.models import Profile, Project

    other_user_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Profile(
                id=other_user_id,
                display_name="Someone Else",
                email=f"{other_user_id}@example.com",
                password_hash="x",
            )
        )
        session.add(
            Project(id=other_project_id, user_id=other_user_id, name="Not yours")
        )
        await session.commit()

    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    response = await db_client.patch(
        f"/chats/{chat['id']}", json={"project_id": str(other_project_id)}
    )

    assert response.status_code == 404


async def test_update_with_no_fields_is_rejected(db_client):
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])

    response = await db_client.patch(f"/chats/{chat['id']}", json={})

    assert response.status_code == 422


async def test_delete_removes_the_chat_and_its_messages(db_client):
    project = await _make_project(db_client)
    chat = await _make_chat(db_client, project["id"])
    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello"})

    response = await db_client.delete(f"/chats/{chat['id']}")

    assert response.status_code == 204
    assert (await db_client.get(f"/chats/{chat['id']}")).status_code == 404


async def test_cannot_touch_someone_elses_chat(db_client):
    """PATCH and DELETE go through the same ownership-checked _get_chat as
    every other chat route -- someone else's chat id 404s, not 403, same
    as everywhere else in this API."""
    import uuid

    fake_chat_id = uuid.uuid4()

    assert (
        await db_client.patch(f"/chats/{fake_chat_id}", json={"title": "x"})
    ).status_code == 404
    assert (await db_client.delete(f"/chats/{fake_chat_id}")).status_code == 404
