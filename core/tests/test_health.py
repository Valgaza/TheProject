"""The health endpoint reports its own state and its dependencies'."""

from __future__ import annotations


async def test_health_reports_provider_and_model(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake"
    assert body["chat_model"] == "fake-chat-model"


async def test_health_degrades_when_dependencies_are_unreachable(client):
    """Fixtures point at an unresolvable host and no database."""
    body = (await client.get("/health")).json()

    assert body["memory_service"] == "unreachable"
    assert body["rag_service"] == "unreachable"
    assert body["database"] == "unreachable"
    assert body["status"] == "degraded"


async def test_health_reports_a_reachable_database(db_client):
    body = (await db_client.get("/health")).json()

    assert body["database"] == "ok"
