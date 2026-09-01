"""Chats that belong to no project.

A project is a memory scope, so filing a throwaway question under one is not a
neutral act -- it writes into that body of work. These cover the door that lets
a chat exist outside every project, and the ownership check that has to keep
working once there is no project to join through.
"""

from __future__ import annotations

import uuid


async def test_a_chat_can_be_created_with_no_project(db_client):
    response = await db_client.post("/chats", json={"title": "Just asking"})

    assert response.status_code == 201, response.text
    assert response.json()["project_id"] is None


async def test_a_loose_chat_holds_a_full_turn(db_client):
    chat = (await db_client.post("/chats", json={})).json()

    response = await db_client.post(f"/chats/{chat['id']}/messages", json={"content": "hello"})

    assert response.status_code == 201, response.text
    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


async def test_loose_filters_out_project_chats(db_client):
    project = (await db_client.post("/projects", json={"name": "Thesis"})).json()
    filed = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()
    loose = (await db_client.post("/chats", json={})).json()

    everything = [c["id"] for c in (await db_client.get("/chats")).json()]
    only_loose = [c["id"] for c in (await db_client.get("/chats?loose=true")).json()]

    assert filed["id"] in everything and loose["id"] in everything
    assert loose["id"] in only_loose
    assert filed["id"] not in only_loose


async def test_a_chat_created_through_a_project_carries_it(db_client):
    project = (await db_client.post("/projects", json={"name": "Thesis"})).json()

    chat = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()

    assert chat["project_id"] == project["id"]


async def test_creating_a_chat_under_an_unknown_project_is_404(db_client):
    response = await db_client.post("/chats", json={"project_id": str(uuid.uuid4())})

    assert response.status_code == 404


async def test_an_unknown_chat_is_still_404(db_client):
    """Ownership moved off the project join; the check has to survive the move."""
    response = await db_client.get(f"/chats/{uuid.uuid4()}")

    assert response.status_code == 404
