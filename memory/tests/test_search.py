"""Search: the recipe we hand graphiti, and what comes back out."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from graphiti_core.search.search_config import EdgeReranker, NodeReranker

from nexus_memory import main as memory_main
from nexus_memory.main import app
from nexus_memory.retrieval import DEFAULT_LIMIT, build_search_config
from nexus_memory.scopes import project_group, user_group

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


# ── The recipe ────────────────────────────────────────────────────────────────


def test_every_search_gets_its_own_config():
    """Graphiti.search() assigns `search_config.limit = num_results` onto a
    module-level recipe, so two concurrent searches with different limits
    overwrite each other's. Building fresh makes that impossible."""
    first = build_search_config(3)
    second = build_search_config(25)

    assert first is not second
    assert first.limit == 3
    assert second.limit == 25

    first.edge_config.sim_min_score = 0.99
    assert second.edge_config.sim_min_score != 0.99


def test_we_do_not_rerank_with_the_cross_encoder():
    """It costs one LLM classification call per candidate passage, so latency
    scales with how much the graph knows. RRF is arithmetic over ranks bm25 and
    cosine already produced."""
    config = build_search_config()

    assert config.edge_config.reranker == EdgeReranker.rrf
    assert config.node_config.reranker == NodeReranker.rrf


def test_nodes_are_searched_as_well_as_edges():
    """Ingesting "Aarya is a final year B.Tech student who prefers terse
    answers" produced no edges at all -- graphiti stored it as one node's
    summary, because it is attributes of a single entity rather than a
    relationship between two. Edge-only retrieval misses exactly the standing
    personal facts the user tier exists to hold."""
    config = build_search_config()

    assert config.node_config is not None
    assert config.edge_config is not None


def test_episodes_and_communities_are_excluded():
    """Episodes are the raw text a fact was distilled from, so including both
    says everything twice. Communities are never built."""
    config = build_search_config()

    assert config.episode_config is None
    assert config.community_config is None


# ── The endpoint ──────────────────────────────────────────────────────────────


class FakeEdge:
    def __init__(self, fact, group_id, *, invalid_at=None, expired_at=None):
        self.uuid = str(uuid.uuid4())
        self.fact = fact
        self.name = "RELATES_TO"
        self.group_id = group_id
        self.invalid_at = invalid_at
        self.expired_at = expired_at
        self.valid_at = None


class FakeNode:
    def __init__(self, name, summary, group_id):
        self.uuid = str(uuid.uuid4())
        self.name = name
        self.summary = summary
        self.group_id = group_id


class FakeResults:
    def __init__(self, edges, nodes):
        self.edges = edges
        self.nodes = nodes


class FakeGraphiti:
    def __init__(self, edges, nodes):
        self._edges = edges
        self._nodes = nodes
        self.calls: list[dict] = []

    async def search_(self, *, query, config, group_ids):
        self.calls.append({"query": query, "config": config, "group_ids": group_ids})
        return FakeResults(self._edges, self._nodes)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _install(monkeypatch, edges=(), nodes=()) -> FakeGraphiti:
    fake = FakeGraphiti(list(edges), list(nodes))
    monkeypatch.setattr(memory_main, "_graphiti", fake)
    return fake


async def test_a_search_unions_both_tiers(client, monkeypatch):
    fake = _install(monkeypatch, [])

    await client.post(
        "/search",
        json={"query": "what storage?", "user_id": str(ALICE), "project_id": str(THESIS)},
    )

    assert fake.calls[0]["group_ids"] == [
        user_group(ALICE),
        project_group(ALICE, THESIS),
    ]


async def test_facts_say_which_tier_they_came_from(client, monkeypatch):
    _install(
        monkeypatch,
        [
            FakeEdge("Aarya prefers terse answers", user_group(ALICE)),
            FakeEdge("the graph store is Neo4j", project_group(ALICE, THESIS)),
        ],
    )

    response = await client.post(
        "/search",
        json={"query": "storage", "user_id": str(ALICE), "project_id": str(THESIS)},
    )

    scopes = {f["fact"]: f["scope"] for f in response.json()["facts"]}
    assert scopes == {
        "Aarya prefers terse answers": "user",
        "the graph store is Neo4j": "project",
    }


async def test_superseded_facts_are_not_returned(client, monkeypatch):
    """graphiti keeps an invalidated edge rather than deleting it. Right for
    history, wrong for context: injecting a fact that stopped being true makes
    the assistant confidently stale."""
    _install(
        monkeypatch,
        [
            FakeEdge("the graph store is Neo4j", project_group(ALICE, THESIS)),
            FakeEdge(
                "the graph store is SQLite",
                project_group(ALICE, THESIS),
                invalid_at=datetime.now(timezone.utc),
            ),
            FakeEdge(
                "the graph store is Postgres",
                project_group(ALICE, THESIS),
                expired_at=datetime.now(timezone.utc),
            ),
        ],
    )

    response = await client.post(
        "/search",
        json={"query": "storage", "user_id": str(ALICE), "project_id": str(THESIS)},
    )

    facts = [f["fact"] for f in response.json()["facts"]]
    assert facts == ["the graph store is Neo4j"]


async def test_the_limit_reaches_the_config(client, monkeypatch):
    fake = _install(monkeypatch, [])

    await client.post(
        "/search", json={"query": "x", "user_id": str(ALICE), "limit": 3}
    )

    assert fake.calls[0]["config"].limit == 3


async def test_the_default_limit_applies(client, monkeypatch):
    fake = _install(monkeypatch, [])

    await client.post("/search", json={"query": "x", "user_id": str(ALICE)})

    assert fake.calls[0]["config"].limit == DEFAULT_LIMIT


async def test_a_blank_query_is_refused(client, monkeypatch):
    fake = _install(monkeypatch, [])

    response = await client.post(
        "/search", json={"query": "   ", "user_id": str(ALICE)}
    )

    assert response.status_code == 422
    assert fake.calls == []


async def test_an_absurd_limit_is_refused(client, monkeypatch):
    _install(monkeypatch, [])

    response = await client.post(
        "/search", json={"query": "x", "user_id": str(ALICE), "limit": 5000}
    )

    assert response.status_code == 422


async def test_a_node_summary_comes_back_as_a_recollection(client, monkeypatch):
    """The regression that made this phase's third scenario fail: a standing
    fact about a person exists only as a node summary."""
    _install(
        monkeypatch,
        nodes=[
            FakeNode(
                "Aarya",
                "Aarya is a final year B.Tech student who prefers terse answers.",
                user_group(ALICE),
            )
        ],
    )

    response = await client.post(
        "/search", json={"query": "what do you know about me?", "user_id": str(ALICE)}
    )

    facts = response.json()["facts"]
    assert len(facts) == 1
    assert facts[0]["kind"] == "entity"
    assert facts[0]["subject"] == "Aarya"
    assert "B.Tech" in facts[0]["fact"]


async def test_the_same_sentence_is_not_returned_three_times(client, monkeypatch):
    """One ingested sentence becomes an edge plus a summary on each endpoint
    node. Saying it three times in a prompt reads as emphasis nobody meant."""
    sentence = "The Nexus thesis project uses Neo4j as its graph store."
    group = project_group(ALICE, THESIS)
    _install(
        monkeypatch,
        edges=[FakeEdge(sentence, group)],
        nodes=[
            FakeNode("Neo4j", sentence, group),
            FakeNode("The Nexus thesis project", sentence, group),
        ],
    )

    response = await client.post(
        "/search",
        json={"query": "storage", "user_id": str(ALICE), "project_id": str(THESIS)},
    )

    facts = response.json()["facts"]
    assert len(facts) == 1
    # The edge wins over the node summaries that duplicate it.
    assert facts[0]["kind"] == "relationship"


async def test_a_node_with_no_summary_is_skipped(client, monkeypatch):
    _install(monkeypatch, nodes=[FakeNode("Nameless", "", user_group(ALICE))])

    response = await client.post(
        "/search", json={"query": "x", "user_id": str(ALICE)}
    )

    assert response.json()["facts"] == []


async def test_the_limit_caps_the_merged_result(client, monkeypatch):
    _install(
        monkeypatch,
        edges=[FakeEdge(f"fact {i}", user_group(ALICE)) for i in range(5)],
        nodes=[FakeNode(f"n{i}", f"summary {i}", user_group(ALICE)) for i in range(5)],
    )

    response = await client.post(
        "/search", json={"query": "x", "user_id": str(ALICE), "limit": 3}
    )

    assert len(response.json()["facts"]) == 3
