"""Chats with no project -- created from /chat directly, not from within
a project. POST/GET /chats (no project in the path), same shape as the
project-scoped POST/GET /projects/{id}/chats."""

from __future__ import annotations


async def test_create_an_unfiled_chat(db_client):
    response = await db_client.post("/chats", json={"title": "Quick question"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Quick question"
    assert body["project_id"] is None


async def test_an_unfiled_chat_is_reachable_by_id(db_client):
    chat = (await db_client.post("/chats", json={})).json()

    response = await db_client.get(f"/chats/{chat['id']}")

    assert response.status_code == 200
    assert response.json()["project_id"] is None


async def test_list_chats_returns_filed_and_unfiled_alike(db_client):
    """GET /chats is every chat this user has -- the sidebar needs the lot
    in one call, so it can nest each one under its project."""
    await db_client.post("/chats", json={"title": "Unfiled one"})
    project = (await db_client.post("/projects", json={"name": "P"})).json()
    await db_client.post(f"/projects/{project['id']}/chats", json={"title": "Filed one"})

    response = await db_client.get("/chats")

    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert "Unfiled one" in titles
    assert "Filed one" in titles


async def test_loose_narrows_the_list_to_unfiled_chats(db_client):
    """``?loose=true`` is what excludes chats filed under a project."""
    await db_client.post("/chats", json={"title": "Unfiled one"})
    project = (await db_client.post("/projects", json={"name": "P"})).json()
    await db_client.post(f"/projects/{project['id']}/chats", json={"title": "Filed one"})

    response = await db_client.get("/chats", params={"loose": "true"})

    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert "Unfiled one" in titles
    assert "Filed one" not in titles


async def test_an_unfiled_chat_can_answer_a_message(db_client, provider):
    """The full turn -- routing, the model call, the write gate's
    background task -- has to work with no project at all, not just chat
    creation on its own."""
    chat = (await db_client.post("/chats", json={})).json()

    response = await db_client.post(
        f"/chats/{chat['id']}/messages", json={"content": "hello from nowhere in particular"}
    )

    assert response.status_code == 201, response.text
    assert response.json()["assistant_message"]["content"] == "fake reply"


async def test_an_unfiled_chat_can_be_moved_into_a_project(db_client):
    chat = (await db_client.post("/chats", json={})).json()
    project = (await db_client.post("/projects", json={"name": "P"})).json()

    response = await db_client.patch(f"/chats/{chat['id']}", json={"project_id": project["id"]})

    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == project["id"]


async def test_deleting_a_project_does_not_touch_unfiled_chats(db_client):
    unfiled = (await db_client.post("/chats", json={"title": "Survives"})).json()
    project = (await db_client.post("/projects", json={"name": "P"})).json()
    filed = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()

    await db_client.delete(f"/projects/{project['id']}")

    assert (await db_client.get(f"/chats/{unfiled['id']}")).status_code == 200
    assert (await db_client.get(f"/chats/{filed['id']}")).status_code == 404
