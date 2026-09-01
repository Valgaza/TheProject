"""Which groups each graph view actually queries.

The endpoints are thin, but the thing they are thin over is the distinction
between "what may this read see" (always the union) and "what is this panel
drawing" (one tier, or the union). Getting those two confused is invisible on
screen -- the graph still renders, it just quietly answers a question nobody
asked.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from nexus_memory import main as memory_main
from nexus_memory.main import GraphResponse, delete_graph, get_graph, ws_graph
from nexus_memory.scopes import project_group, read_groups, user_group

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def queried(monkeypatch):
    """Record the groups each request resolves to, without touching Neo4j."""
    seen: list[list[str]] = []

    async def _build(groups):
        seen.append(list(groups))
        return GraphResponse(group_ids=list(groups), nodes=[], edges=[])

    monkeypatch.setattr(memory_main, "_build_graph_response", _build)
    return seen


async def test_the_user_view_queries_the_universal_tier_alone(queried):
    await get_graph(user_id=ALICE, project_id=THESIS, view="user")

    assert queried == [[user_group(ALICE)]]


async def test_the_project_view_leaves_the_universal_tier_out(queried):
    await get_graph(user_id=ALICE, project_id=THESIS, view="project")

    assert queried == [[project_group(ALICE, THESIS)]]


async def test_the_default_view_is_still_the_union(queried):
    await get_graph(user_id=ALICE, project_id=THESIS)

    assert queried == [read_groups(ALICE, THESIS)]


async def test_a_project_view_with_no_project_is_a_422(queried):
    with pytest.raises(HTTPException) as raised:
        await get_graph(user_id=ALICE, project_id=None, view="project")

    assert raised.value.status_code == 422
    assert queried == []


async def test_deleting_a_project_view_spares_the_universal_tier(monkeypatch):
    """The reset button used to always clear the union.

    Wiping a project's memory should not take everything durable about the
    person with it -- and that loss is silent, because the panel showing one
    project looks exactly the same afterwards either way.
    """
    deleted: list[list[str]] = []

    class FakeDriver:
        async def execute_query(self, _query, groups):
            deleted.append(list(groups))

    class FakeGraphiti:
        driver = FakeDriver()

    monkeypatch.setattr(memory_main, "_require_graphiti", lambda: FakeGraphiti())

    await delete_graph(user_id=ALICE, project_id=THESIS, view="project")

    assert deleted == [[project_group(ALICE, THESIS)]]
    assert user_group(ALICE) not in deleted[0]


async def test_a_socket_asking_for_an_impossible_view_is_closed(fake_websocket):
    """Refused at the handshake, rather than silently widened to the union."""
    socket = fake_websocket()
    closed: list[int] = []

    async def _close(code, reason):
        closed.append(code)

    socket.close = _close

    await ws_graph(socket, user_id=ALICE, project_id=None, view="project")

    assert closed == [1008]
    assert socket.sent == []
