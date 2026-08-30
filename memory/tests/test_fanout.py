"""Who receives a graph update when a write lands.

The bug this covers: subscriptions used to be keyed by a single group id, so a
client watching a project never saw a write that landed in the user tier. With
two-tier scoping that is not a rare edge -- durable facts land in the user tier
constantly -- and the symptom is memory forming while the panel sits still.
"""

from __future__ import annotations

import json
import uuid

import pytest

from nexus_memory import main as memory_main
from nexus_memory.main import ConnectionManager, GraphResponse
from nexus_memory.scopes import project_group, read_groups, user_group


ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SIDE = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture
def manager() -> ConnectionManager:
    return ConnectionManager()


@pytest.fixture
def fake_graph(monkeypatch):
    """Stand in for the Neo4j round trip, recording which groups were queried."""
    queried: list[list[str]] = []

    async def _build(groups):
        queried.append(list(groups))
        return GraphResponse(group_ids=list(groups), nodes=[], edges=[])

    monkeypatch.setattr(memory_main, "_build_graph_response", _build)
    return queried


async def test_a_project_watcher_sees_a_user_scope_write(
    manager, fake_graph, monkeypatch, fake_websocket
):
    """The regression this whole change exists for."""
    monkeypatch.setattr(memory_main, "manager", manager)
    socket = fake_websocket()
    await manager.connect(socket, read_groups(ALICE, THESIS))

    await memory_main._broadcast_write(user_group(ALICE))

    assert len(socket.sent) == 1


async def test_a_watcher_receives_its_whole_union_not_just_the_written_group(
    manager, fake_graph, monkeypatch, fake_websocket
):
    monkeypatch.setattr(memory_main, "manager", manager)
    socket = fake_websocket()
    await manager.connect(socket, read_groups(ALICE, THESIS))

    await memory_main._broadcast_write(user_group(ALICE))

    payload = json.loads(socket.sent[0])
    assert payload["group_ids"] == sorted(
        [user_group(ALICE), project_group(ALICE, THESIS)]
    )


async def test_a_write_to_one_project_does_not_reach_another(
    manager, fake_graph, monkeypatch, fake_websocket
):
    monkeypatch.setattr(memory_main, "manager", manager)
    thesis_watcher = fake_websocket()
    side_watcher = fake_websocket()
    await manager.connect(thesis_watcher, read_groups(ALICE, THESIS))
    await manager.connect(side_watcher, read_groups(ALICE, SIDE))

    await memory_main._broadcast_write(project_group(ALICE, THESIS))

    assert len(thesis_watcher.sent) == 1
    assert side_watcher.sent == []


async def test_a_user_scope_write_reaches_every_project_watcher(
    manager, fake_graph, monkeypatch, fake_websocket
):
    monkeypatch.setattr(memory_main, "manager", manager)
    thesis_watcher = fake_websocket()
    side_watcher = fake_websocket()
    await manager.connect(thesis_watcher, read_groups(ALICE, THESIS))
    await manager.connect(side_watcher, read_groups(ALICE, SIDE))

    await memory_main._broadcast_write(user_group(ALICE))

    assert len(thesis_watcher.sent) == 1
    assert len(side_watcher.sent) == 1


async def test_watchers_sharing_a_scope_set_share_one_query(
    manager, fake_graph, monkeypatch, fake_websocket
):
    """Two browser tabs on the same project must not double the Neo4j work."""
    monkeypatch.setattr(memory_main, "manager", manager)
    for _ in range(3):
        await manager.connect(fake_websocket(), read_groups(ALICE, THESIS))

    await memory_main._broadcast_write(user_group(ALICE))

    assert len(fake_graph) == 1


async def test_a_broken_socket_is_dropped_and_does_not_block_the_others(
    manager, fake_graph, monkeypatch, fake_websocket
):
    monkeypatch.setattr(memory_main, "manager", manager)
    healthy = fake_websocket()
    broken = fake_websocket(broken=True)
    await manager.connect(broken, read_groups(ALICE, THESIS))
    await manager.connect(healthy, read_groups(ALICE, THESIS))

    await memory_main._broadcast_write(user_group(ALICE))

    assert len(healthy.sent) == 1
    remaining = [s.websocket for s in manager.watchers_of(user_group(ALICE))]
    assert remaining == [healthy]


async def test_nobody_watching_costs_nothing(manager, fake_graph, monkeypatch):
    monkeypatch.setattr(memory_main, "manager", manager)

    await memory_main._broadcast_write(user_group(ALICE))

    assert fake_graph == []


async def test_disconnect_stops_delivery(manager, fake_graph, monkeypatch, fake_websocket):
    monkeypatch.setattr(memory_main, "manager", manager)
    socket = fake_websocket()
    subscription = await manager.connect(socket, read_groups(ALICE, THESIS))

    manager.disconnect(subscription)
    await memory_main._broadcast_write(user_group(ALICE))

    assert socket.sent == []
