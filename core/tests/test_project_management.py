"""Rename, pin/unpin, and delete -- the project card's three-dot menu."""

from __future__ import annotations

import uuid


async def _make_project(client, name: str = "Home") -> dict:
    response = await client.post("/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_rename_updates_the_name(db_client):
    project = await _make_project(db_client)

    response = await db_client.patch(f"/projects/{project['id']}", json={"name": "Renamed"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed"
    assert (await db_client.get(f"/projects/{project['id']}")).json()["name"] == "Renamed"


async def test_pin_and_unpin(db_client):
    project = await _make_project(db_client)
    assert project["pinned"] is False

    pinned = (
        await db_client.patch(f"/projects/{project['id']}", json={"pinned": True})
    ).json()
    assert pinned["pinned"] is True

    unpinned = (
        await db_client.patch(f"/projects/{project['id']}", json={"pinned": False})
    ).json()
    assert unpinned["pinned"] is False


async def test_update_with_no_fields_is_rejected(db_client):
    project = await _make_project(db_client)

    response = await db_client.patch(f"/projects/{project['id']}", json={})

    assert response.status_code == 422


async def test_delete_removes_the_project_and_its_chats(db_client):
    project = await _make_project(db_client)
    chat = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()
    await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello"})

    response = await db_client.delete(f"/projects/{project['id']}")

    assert response.status_code == 204
    assert (await db_client.get(f"/projects/{project['id']}")).status_code == 404
    assert (await db_client.get(f"/chats/{chat['id']}")).status_code == 404


async def test_cannot_touch_someone_elses_project(db_client):
    """Same ownership check every other project route goes through --
    someone else's project id 404s, not 403."""
    fake_project_id = uuid.uuid4()

    assert (
        await db_client.patch(f"/projects/{fake_project_id}", json={"name": "x"})
    ).status_code == 404
    assert (await db_client.delete(f"/projects/{fake_project_id}")).status_code == 404
