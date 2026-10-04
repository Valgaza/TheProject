"""Fixtures for the memory service.

The app's lifespan constructs a real Graphiti and connects to Neo4j, so these
tests never run it. Everything here exercises the scope routing and the
websocket fan-out, which is where the logic lives; graphiti itself is somebody
else's tested code.
"""

from __future__ import annotations

import pytest

# What core would send. Every request to the app needs it now; see
# nexus_memory/service_auth.py.
SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _service_token(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)


class FakeWebSocket:
    """Records what was pushed to it, and can be told to fail."""

    def __init__(self, *, broken: bool = False) -> None:
        self.sent: list[str] = []
        self.accepted = False
        self.broken = broken

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        if self.broken:
            raise ConnectionError("client went away")
        self.sent.append(payload)


@pytest.fixture
def websocket() -> FakeWebSocket:
    return FakeWebSocket()


@pytest.fixture
def fake_websocket():
    """The class itself, so tests can make several.

    Handed over as a fixture rather than imported: `from conftest import ...`
    resolves by module name, and there is more than one conftest on the path.
    """
    return FakeWebSocket
