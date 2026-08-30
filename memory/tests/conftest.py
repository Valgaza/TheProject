"""Fixtures for the memory service.

The app's lifespan constructs a real Graphiti and connects to Neo4j, so these
tests never run it. Everything here exercises the scope routing and the
websocket fan-out, which is where the logic lives; graphiti itself is somebody
else's tested code.
"""

from __future__ import annotations

import pytest


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
