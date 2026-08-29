"""Test fixtures.

The app's lifespan builds a real provider from a real API key, so tests bypass
it and populate ``app.state`` directly with fakes. That keeps the default suite
free, offline, and fast.
"""

from __future__ import annotations

from typing import Sequence

import httpx
import pytest

from app.main import app
from app.providers.base import Message, Provider


class FakeProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.chat_calls: list[list[Message]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.chat_calls.append(list(messages))
        return "fake reply"

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        return [[0.0, 0.0, 0.0, 0.0] for _ in texts]


class FakeSettings:
    chat_model = "fake-chat-model"
    embed_model = "fake-embed-model"
    gate_model = "fake-gate-model"
    # A host that cannot resolve, so the health check exercises its failure path.
    memory_service_url = "http://memory.invalid"


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def client(provider: FakeProvider):
    app.state.settings = FakeSettings()
    app.state.provider = provider
    app.state.http = httpx.AsyncClient(timeout=1.0)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.http.aclose()
