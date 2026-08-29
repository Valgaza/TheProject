"""The health endpoint reports its own state and the memory service's."""

from __future__ import annotations


async def test_health_reports_provider_and_model(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "fake"
    assert body["chat_model"] == "fake-chat-model"


async def test_health_degrades_when_memory_service_is_unreachable(client):
    """Fixtures point at an unresolvable host, so this is the failure path."""
    body = (await client.get("/health")).json()

    assert body["memory_service"] == "unreachable"
    assert body["status"] == "degraded"
