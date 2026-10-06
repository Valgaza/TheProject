"""Retiring a fact the person has since changed.

"The deadline is the 14th", then "it moved to the 21st": graphiti keeps both,
as two entity summaries with nothing marking one as out of date. Once core's
gate says the second replaces the first, the first is retired: kept as
history, never returned again.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from nexus_memory import hygiene
from nexus_memory import main as memory_main
from nexus_memory.main import app
from nexus_memory.scopes import project_group, user_group

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakeDriver:
    """Neo4j, holding nodes and edges and answering the retire queries."""

    def __init__(self, nodes=(), edges=()):
        # (uuid, group, is_entity, text)
        self.nodes = list(nodes)
        self.edges = list(edges)  # (uuid, group, text)
        self.marked: set[str] = set()

    async def execute_query(self, query, **params):
        if "RETURN n.uuid AS uuid, 'Entity'" in query:
            return [{"uuid": u, "entity": e, "text": t} for u, g, e, t in self.nodes
                    if g in params["groups"] and u not in self.marked], None, None
        if "RETURN e.uuid AS uuid, coalesce(e.fact" in query:
            return [{"uuid": u, "text": t} for u, g, t in self.edges
                    if g in params["groups"] and u not in self.marked], None, None
        if "SET" in query:
            self.marked.update(params["ids"])
            return [], None, None
        return [{"uuid": u} for u in self.marked], None, None


class FakeGraphiti:
    def __init__(self, driver, nodes=()):
        self.driver = driver
        self.nodes = list(nodes)

    async def search_(self, *, query, config, group_ids):
        return type("R", (), {"edges": [], "nodes": self.nodes, "episodes": []})()


class Node:
    def __init__(self, summary, group_id):
        self.uuid, self.name, self.summary, self.group_id = str(uuid.uuid4()), "n", summary, group_id


@pytest.fixture(autouse=True)
def _clean():
    hygiene._retired.clear()
    yield
    hygiene._retired.clear()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"X-Service-Token": "test-service-token"}) as c:
        yield c


async def test_a_retired_fact_is_no_longer_found(client, monkeypatch):
    g = project_group(ALICE, THESIS)
    old = Node("Alice's project deadline is the 14th of November.", g)
    new = Node("Alice's project deadline is the 21st of November.", g)
    driver = FakeDriver(nodes=[(old.uuid, g, True, old.summary), (new.uuid, g, True, new.summary)])
    monkeypatch.setattr(memory_main, "_graphiti", FakeGraphiti(driver, [old, new]))

    retired = (await client.post("/retire", json={
        "user_id": str(ALICE), "project_id": str(THESIS), "facts": [old.summary]})).json()["retired"]
    facts = (await client.post("/search", json={
        "query": "deadline", "user_id": str(ALICE), "project_id": str(THESIS)})).json()["facts"]

    assert retired == [old.uuid]
    assert [f["fact"] for f in facts] == [new.summary]


async def test_every_copy_of_the_sentence_goes_and_nothing_rewritten_does():
    """Found on the live graph: the episode carrying the same sentence came
    back once its entity was retired; and a node graphiti had since rewritten
    to the new value must survive."""
    g = project_group(ALICE, THESIS)
    driver = FakeDriver(nodes=[
        ("entity-14", g, True, "Alice's project deadline is the 14th of November."),
        ("episode-14", g, False, "Alice's project deadline is the 14th of November"),
        ("rewritten", g, True, "Alice's project deadline is the 21st of November."),
    ])
    retired = await hygiene.retire(driver, ["Alice's project deadline is the 14th of November."], [g])
    assert sorted(retired) == ["entity-14", "episode-14"]


async def test_a_summary_that_says_several_things_is_never_retired():
    g = user_group(ALICE)
    text = "Alice is 21 years old.\nAlice's father is Subhash."
    driver = FakeDriver(nodes=[("alice", g, True, text)])
    assert await hygiene.retire(driver, [text], [g]) == []


async def test_nobody_can_retire_someone_else_s_facts(client, monkeypatch):
    theirs = Node("Bob prefers tea.", user_group(BOB))
    driver = FakeDriver(nodes=[(theirs.uuid, theirs.group_id, True, theirs.summary)])
    monkeypatch.setattr(memory_main, "_graphiti", FakeGraphiti(driver, [theirs]))

    retired = (await client.post("/retire", json={"user_id": str(ALICE), "facts": [theirs.summary]})).json()
    assert retired["retired"] == [] and not hygiene.is_retired(theirs.uuid)


async def test_the_retired_set_survives_a_restart():
    driver = FakeDriver(nodes=[("a", "g", False, "x")])
    await hygiene.retire(driver, ["x"], ["g"])
    hygiene._retired.clear()
    assert await hygiene.load_retired(driver) == 1 and hygiene.is_retired("a")


async def test_the_graph_view_shows_what_is_current(monkeypatch):
    """The graph panel showed both dates as live nodes; a retired one, and any
    edge touching it, is left out of the picture."""
    g = project_group(ALICE, THESIS)

    class N:
        def __init__(self, name):
            self.uuid, self.name, self.labels, self.summary, self.attributes, self.group_id = (
                name, name, ["Entity"], name, {}, g)

    class E:
        def __init__(self, uuid_, source, target):
            self.uuid, self.source_node_uuid, self.target_node_uuid = uuid_, source, target
            self.name, self.fact, self.group_id = "HAS_DEADLINE", "x", g

    nodes = [N("project"), N("14th of November"), N("21st of November")]
    edges = [E("e14", "project", "14th of November"), E("e21", "project", "21st of November")]

    async def all_nodes(driver, groups):
        return nodes

    async def all_edges(driver, groups):
        return edges

    monkeypatch.setattr(memory_main.EntityNode, "get_by_group_ids", all_nodes)
    monkeypatch.setattr(memory_main.EntityEdge, "get_by_group_ids", all_edges)
    monkeypatch.setattr(memory_main, "_graphiti", FakeGraphiti(FakeDriver()))
    hygiene._retired.add("14th of November")

    graph = await memory_main._build_graph_response([g])

    assert [n.name for n in graph.nodes] == ["project", "21st of November"]
    assert [e.id for e in graph.edges] == ["e21"]
