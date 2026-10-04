"""The graph panel goes through core now, and core decides whose graph it is.

What these pin down is the thing the browser can no longer do: name a user.
Every upstream call carries the session's id, a project has to belong to that
session, and a socket from the wrong origin or without a cookie never opens.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nexus_core.auth import SESSION_COOKIE_NAME, create_session_token
from nexus_core.main import app
from nexus_core.routers import graph as graph_router

# The seeded demo profile, which db_client is logged in as.
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class FakeMemory:
    """Records what core asked memory for."""

    def __init__(self) -> None:
        self.reads: list[dict] = []
        self.deletes: list[dict] = []

    async def graph(self, **kwargs) -> dict:
        self.reads.append(kwargs)
        return {"group_ids": [], "nodes": [], "edges": []}

    async def delete_graph(self, **kwargs) -> None:
        self.deletes.append(kwargs)

    def graph_socket_url(self, *, user_id, project_id, view) -> str:
        return f"ws://memory.test/ws/graph/{user_id}?view={view}"


@pytest.fixture
def memory(db_client) -> FakeMemory:
    fake = FakeMemory()
    db_client._transport.app.state.memory = fake
    return fake


async def test_the_graph_is_read_for_the_session_user(db_client, memory):
    response = await db_client.get("/graph", params={"view": "user"})

    assert response.status_code == 200, response.text
    assert memory.reads == [{"user_id": DEMO_USER_ID, "project_id": None, "view": "user"}]


async def test_no_session_means_no_graph(db_client, memory):
    db_client.cookies.clear()

    response = await db_client.get("/graph")

    assert response.status_code == 401
    assert memory.reads == []


async def test_a_user_id_in_the_query_is_ignored(db_client, memory):
    """The old API took one. Passing it now must not change whose graph it is."""
    await db_client.get("/graph", params={"user_id": str(uuid.uuid4())})

    assert memory.reads[0]["user_id"] == DEMO_USER_ID


async def test_someone_elses_project_is_not_found(db_client, memory):
    owned = (await db_client.post("/projects", json={"name": "Mine"})).json()
    await db_client.post(
        "/auth/signup",
        json={"email": f"{uuid.uuid4().hex}@example.com", "password": "long-enough-pw", "display_name": "Eve"},
    )

    response = await db_client.get("/graph", params={"view": "project", "project_id": owned["id"]})
    wipe = await db_client.delete("/graph", params={"view": "project", "project_id": owned["id"]})

    assert response.status_code == 404
    assert wipe.status_code == 404
    assert memory.reads == [] and memory.deletes == []


async def test_the_project_view_needs_a_project(db_client, memory):
    response = await db_client.get("/graph", params={"view": "project"})
    assert response.status_code == 422


async def test_a_reset_wipes_only_the_session_users_graph(db_client, memory):
    response = await db_client.delete("/graph", params={"view": "user"})

    assert response.status_code == 204
    assert memory.deletes == [{"user_id": DEMO_USER_ID, "project_id": None, "view": "user"}]


# ── The socket ───────────────────────────────────────────────────────────────


class FakeUpstream:
    """Stands in for memory's socket: one snapshot, then it closes."""

    def __init__(self, url: str, headers: dict) -> None:
        self.url, self.headers = url, headers
        self.closed = False

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        yield '{"nodes": []}'

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def socket_app(monkeypatch, db_client):
    """Rides on db_client for the app state it sets up, then speaks websocket
    through Starlette's client, which httpx cannot."""
    settings = app.state.settings
    app.state.settings = settings
    app.state.memory = FakeMemory()
    dialled: list[FakeUpstream] = []

    async def connect(url, additional_headers):
        upstream = FakeUpstream(url, additional_headers)
        dialled.append(upstream)
        return upstream

    monkeypatch.setattr(graph_router.websockets, "connect", connect)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(DEMO_USER_ID, settings))
    return client, dialled


def test_the_socket_relays_memory_for_the_session_user(socket_app):
    client, dialled = socket_app

    with client.websocket_connect(
        "/ws/graph?view=user", headers={"origin": "http://localhost:3000"}
    ) as ws:
        assert ws.receive_text() == '{"nodes": []}'

    assert f"/ws/graph/{DEMO_USER_ID}" in dialled[0].url
    assert dialled[0].headers == {"X-Service-Token": "test-service-token"}


def test_another_origin_cannot_open_the_socket(socket_app):
    """Browsers attach cookies to websocket handshakes and apply no CORS to
    them, so this check is the only thing between another tab and the graph."""
    client, dialled = socket_app

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/graph", headers={"origin": "https://evil.example"}):
            pass

    assert closed.value.code == 1008
    assert dialled == []


def test_no_session_means_no_socket(socket_app):
    client, dialled = socket_app
    client.cookies.clear()

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/graph", headers={"origin": "http://localhost:3000"}):
            pass

    assert closed.value.code == 1008
    assert dialled == []
