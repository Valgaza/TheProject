"""Fixtures for the end-to-end scenario suite.

These drive the whole stack -- core, memory, Neo4j, Postgres and a real model --
so they cost money and minutes, and they are skipped unless you ask for them:

    RUN_SCENARIOS=1 uv run pytest tests -v -s

Everything else in the repo is unit-level and runs in about two seconds. This
suite exists to answer the questions those cannot: does a fact stated in one
chat come back in another, does it stay out of a different project, and does
the write gate keep chit-chat out of the graph.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

CORE_URL = os.getenv("CORE_URL", "http://localhost:8100")
MEMORY_URL = os.getenv("MEMORY_URL", "http://localhost:8000")
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "00000000-0000-0000-0000-000000000001")

# graphiti's extraction is 12-30s per episode, and the gate adds a model call in
# front of it. Generous, because a flaky timeout here reads as a product bug.
INGEST_TIMEOUT = 180.0
POLL_INTERVAL = 2.0

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_SCENARIOS"),
    reason="drives the live stack and spends API credit; set RUN_SCENARIOS=1",
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=180.0) as c:
        for url in (f"{CORE_URL}/health", f"{MEMORY_URL}/health"):
            try:
                response = await c.get(url, timeout=5.0)
            except Exception as exc:
                pytest.skip(f"{url} unreachable ({exc.__class__.__name__})")
            if response.json().get("status") not in ("ok", "degraded"):
                pytest.skip(f"{url} reports {response.json()}")
        yield c


@pytest.fixture
async def clean_user(client: httpx.AsyncClient):
    """Start from an empty user tier.

    Project tiers are left alone: every scenario makes its own project, so they
    are empty by construction, and wiping every project would destroy whatever
    the developer was looking at.
    """
    await client.delete(f"{MEMORY_URL}/graph", params={"user_id": DEMO_USER_ID})
    yield DEMO_USER_ID
    await client.delete(f"{MEMORY_URL}/graph", params={"user_id": DEMO_USER_ID})


class Conversation:
    """A chat, with the waiting-for-memory logic these scenarios all need."""

    def __init__(self, client: httpx.AsyncClient, project_id: str, chat_id: str) -> None:
        self._client = client
        self.project_id = project_id
        self.chat_id = chat_id
        self.turns: list[dict] = []

    async def say(self, text: str) -> dict:
        response = await self._client.post(
            f"{CORE_URL}/chats/{self.chat_id}/messages", json={"content": text}
        )
        response.raise_for_status()
        turn = response.json()
        self.turns.append(turn)
        return turn

    async def recalled(self, text: str) -> list[str]:
        turn = await self.say(text)
        return [fact["fact"] for fact in turn["recalled"]]

    async def wait_for_memory(self, expected_writes: int) -> list[dict]:
        """Block until the write gate has finished with every turn so far.

        Polls the provenance table rather than sleeping a guessed interval:
        a row appears per persisted fact and moves off "pending" when the
        episode is in the graph. Sleeping would be both slower and flakier.
        """
        deadline = asyncio.get_event_loop().time() + INGEST_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            episodes = await self.episodes()
            settled = [e for e in episodes if e["status"] != "pending"]
            if len(settled) >= expected_writes:
                # A short grace period, so a gate that wrongly persists an extra
                # turn is caught here rather than passing silently.
                await asyncio.sleep(POLL_INTERVAL)
                return await self.episodes()
            await asyncio.sleep(POLL_INTERVAL)

        raise AssertionError(
            f"memory did not settle within {INGEST_TIMEOUT}s "
            f"(wanted {expected_writes}, got {await self.episodes()})"
        )

    async def episodes(self) -> list[dict]:
        response = await self._client.get(
            f"{CORE_URL}/chats/{self.chat_id}/memory-episodes"
        )
        response.raise_for_status()
        return response.json()


@pytest.fixture
async def make_conversation(client: httpx.AsyncClient):
    """Build fresh projects and chats, named so leftovers are identifiable."""

    async def _make(project_id: str | None = None, *, label: str = "scenario"):
        if project_id is None:
            project = (
                await client.post(
                    f"{CORE_URL}/projects",
                    json={"name": f"[test] {label} {uuid.uuid4().hex[:6]}"},
                )
            ).json()
            project_id = project["id"]
        chat = (
            await client.post(f"{CORE_URL}/projects/{project_id}/chats", json={})
        ).json()
        return Conversation(client, project_id, chat["id"])

    return _make
