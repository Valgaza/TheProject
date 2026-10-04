"""Only core gets in.

The graph used to be readable and wipeable by anyone who knew a user id. These
pin the door shut: no token, wrong token, or no token configured at all means
no graph, over HTTP and over the socket alike.
"""

from __future__ import annotations

import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nexus_memory import main as memory_main
from nexus_memory.main import GraphResponse

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
TOKEN = "core-and-memory-only"


@pytest.fixture
def http(monkeypatch):
    """The real app, minus the lifespan that would dial Neo4j."""
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)

    async def _build(groups):
        return GraphResponse(group_ids=list(groups), nodes=[], edges=[])

    monkeypatch.setattr(memory_main, "_build_graph_response", _build)
    return TestClient(memory_main.app)


def test_a_request_without_the_token_is_refused(http):
    response = http.get("/graph", params={"user_id": str(ALICE)})
    assert response.status_code == 401


def test_a_wrong_token_is_refused(http):
    response = http.get(
        "/graph", params={"user_id": str(ALICE)}, headers={"X-Service-Token": "guess"}
    )
    assert response.status_code == 401


def test_the_right_token_reaches_the_route(http):
    response = http.get(
        "/graph", params={"user_id": str(ALICE)}, headers={"X-Service-Token": TOKEN}
    )
    assert response.status_code == 200


def test_an_unset_token_refuses_everyone_rather_than_admitting_them(http, monkeypatch):
    monkeypatch.delenv("SERVICE_TOKEN")
    response = http.get(
        "/graph", params={"user_id": str(ALICE)}, headers={"X-Service-Token": ""}
    )
    assert response.status_code == 401


def test_the_socket_is_behind_the_same_door(http):
    with pytest.raises(WebSocketDisconnect) as closed:
        with http.websocket_connect(f"/ws/graph/{ALICE}"):
            pass
    assert closed.value.code == 1008


def test_health_stays_open_for_liveness_checks(http, monkeypatch):
    monkeypatch.setattr(memory_main, "_graphiti", None)
    assert http.get("/health").status_code != 401
