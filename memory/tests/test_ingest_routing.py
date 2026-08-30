"""What /ingest does with a scope, at the HTTP boundary."""

from __future__ import annotations

import uuid

import httpx
import pytest

from nexus_memory import main as memory_main
from nexus_memory.main import app
from nexus_memory.scopes import project_group, user_group

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def ingested(monkeypatch) -> list[tuple[str, str, str | None]]:
    """Capture what would have been written, without a 20s graphiti round trip."""
    calls: list[tuple[str, str, str | None]] = []

    async def _run(text, group_id, speaker=None):
        calls.append((text, group_id, speaker))
        return "00000000-0000-0000-0000-0000000000ff", None

    monkeypatch.setattr(memory_main, "_run_ingest", _run)
    return calls


@pytest.fixture
async def client():
    # No lifespan: these tests never touch Neo4j.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_a_user_scope_write_goes_to_the_user_group(client, ingested):
    response = await client.post(
        "/ingest",
        json={"text": "I prefer terse answers", "user_id": str(ALICE), "scope": "user"},
    )

    assert response.status_code == 202
    assert response.json()["group_id"] == user_group(ALICE)
    assert ingested == [("I prefer terse answers", user_group(ALICE), None)]


async def test_a_project_scope_write_goes_to_the_project_group(client, ingested):
    response = await client.post(
        "/ingest",
        json={
            "text": "the graph store is Neo4j",
            "user_id": str(ALICE),
            "project_id": str(THESIS),
            "scope": "project",
        },
    )

    assert response.status_code == 202
    assert response.json()["group_id"] == project_group(ALICE, THESIS)


async def test_a_project_id_alone_does_not_imply_project_scope(client, ingested):
    """Scope is explicit. Inferring it from the presence of a project id would
    make the tier an accident of which fields the caller happened to send."""
    response = await client.post(
        "/ingest",
        json={
            "text": "still a durable fact",
            "user_id": str(ALICE),
            "project_id": str(THESIS),
            "scope": "user",
        },
    )

    assert response.json()["group_id"] == user_group(ALICE)


async def test_project_scope_without_a_project_id_is_refused(client, ingested):
    response = await client.post(
        "/ingest",
        json={"text": "where does this go?", "user_id": str(ALICE), "scope": "project"},
    )

    assert response.status_code == 422
    assert ingested == []


async def test_blank_text_is_refused(client, ingested):
    response = await client.post(
        "/ingest", json={"text": "   ", "user_id": str(ALICE), "scope": "user"}
    )

    assert response.status_code == 422
    assert ingested == []


async def test_a_non_uuid_user_is_refused(client, ingested):
    """The old API took an arbitrary string, which is how "user_001" ended up
    as a group id that no Supabase row corresponds to."""
    response = await client.post(
        "/ingest", json={"text": "hello", "user_id": "user_001", "scope": "user"}
    )

    assert response.status_code == 422


async def test_an_unknown_scope_is_refused(client, ingested):
    response = await client.post(
        "/ingest", json={"text": "hello", "user_id": str(ALICE), "scope": "global"}
    )

    assert response.status_code == 422


async def test_a_statement_is_ingested_without_an_invented_speaker(client, ingested):
    """The graph used to learn that a person named "user" was the subject of
    every fact, because every episode body was prefixed "user: "."""
    await client.post(
        "/ingest",
        json={"text": "Neo4j is the graph store", "user_id": str(ALICE), "scope": "user"},
    )

    text, _, speaker = ingested[0]
    assert speaker is None
    assert text == "Neo4j is the graph store"


async def test_a_named_speaker_is_carried_through(client, ingested):
    await client.post(
        "/ingest",
        json={
            "text": "I prefer terse answers",
            "user_id": str(ALICE),
            "scope": "user",
            "speaker": "Aarya",
        },
    )

    assert ingested[0][2] == "Aarya"


async def test_a_waited_ingest_returns_the_uuid_graphiti_minted(client, ingested):
    """The uuid cannot be supplied by the caller: add_episode's `uuid` argument
    means "update this existing episode" and raises NodeNotFoundError for one it
    has not seen. So it has to come back out."""
    response = await client.post(
        "/ingest",
        json={
            "text": "Aarya uses Neo4j",
            "user_id": str(ALICE),
            "scope": "user",
            "wait": True,
        },
    )

    assert response.json()["episode_uuid"] == "00000000-0000-0000-0000-0000000000ff"


async def test_wait_reports_the_real_outcome(client, monkeypatch):
    async def _ok(text, group_id, speaker=None):
        return "00000000-0000-0000-0000-0000000000ff", None

    monkeypatch.setattr(memory_main, "_run_ingest", _ok)

    response = await client.post(
        "/ingest",
        json={"text": "x", "user_id": str(ALICE), "scope": "user", "wait": True},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "ingested"
    assert response.json()["error"] is None


async def test_wait_surfaces_a_failure_instead_of_swallowing_it(client, monkeypatch):
    async def _boom(text, group_id, speaker=None):
        return None, "RuntimeError: neo4j is down"

    monkeypatch.setattr(memory_main, "_run_ingest", _boom)

    response = await client.post(
        "/ingest",
        json={"text": "x", "user_id": str(ALICE), "scope": "user", "wait": True},
    )

    body = response.json()
    assert body["status"] == "failed"
    assert "neo4j is down" in body["error"]


async def test_without_wait_the_call_is_still_fire_and_forget(client, ingested):
    response = await client.post(
        "/ingest", json={"text": "x", "user_id": str(ALICE), "scope": "user"}
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
