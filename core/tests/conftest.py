"""Test fixtures.

The app's lifespan builds a real provider and a real engine, so tests bypass it
and populate ``app.state`` directly. Model calls are faked; the database is
real, because the point of these tests is the SQL, the constraints and the
triggers, none of which a fake would exercise. Database tests skip themselves
when the local Supabase stack isn't running, so the default suite stays green.
"""

from __future__ import annotations

import os
import uuid
from typing import Sequence

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from nexus_core.auth import SESSION_COOKIE_NAME, create_session_token
from nexus_core.chat_title import TITLE_SYSTEM_PROMPT
from nexus_core.db import create_engine
from nexus_core.main import app
from nexus_core.providers.base import Message, Provider

DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


class FakeProvider(Provider):
    name = "fake"

    def __init__(self) -> None:
        self.chat_calls: list[list[Message]] = []
        # Tracked separately from chat_calls -- chat_service.py fires a title
        # generation call (chat_title.generate) alongside the answer call for
        # a chat's first message, and tests written against chat_calls expect
        # it to hold only real answer prompts, at stable indices.
        self.title_calls: list[list[Message]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if messages and messages[0].get("content") == TITLE_SYSTEM_PROMPT:
            self.title_calls.append(list(messages))
            return "Fake Title"
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
    demo_user_id = DEMO_USER_ID
    # Hosts that cannot resolve, so the health check exercises its failure path.
    memory_service_url = "http://memory.invalid"
    rag_service_url = "http://rag.invalid"
    # The relevance floor documents must clear to reach the model. Matches
    # the production default so a test that stubs a score reads the same way
    # here as it behaves in the app.
    rag_min_score = 0.5
    # A real secret so create_session_token/decode_session_token actually
    # work in tests — doesn't need to match any real deployment's secret,
    # only itself, within one test run.
    jwt_secret = "test-secret-not-used-anywhere-real"
    jwt_algorithm = "HS256"
    session_max_age_seconds = 60 * 60
    session_cookie_secure = False
    google_oauth_enabled = False
    frontend_url = "http://localhost:3000"
    cors_origins = ["http://localhost:3000"]
    service_token = "test-service-token"


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def engine():
    """Skip the whole database suite if the local stack isn't up."""
    eng = create_engine(TEST_DATABASE_URL)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"database unavailable ({exc.__class__.__name__}); run `docker compose up -d postgres`")
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    """Bind sessions to one connection inside a transaction we always roll back.

    ``join_transaction_mode="create_savepoint"`` lets the request handlers call
    ``session.commit()`` normally while the outer transaction still discards
    everything, so tests never pollute the seeded data.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield factory
        await transaction.rollback()


def _reset_optional_clients() -> None:
    """Clear the pipeline clients between tests.

    ``app`` is a module-level object, so ``app.state`` is shared by every test
    in the session: a client left on it by one test is still there for the
    next. Both are read with ``getattr(..., None)``, so a leaked client does
    not error — it silently changes behaviour, which is worse. A test that
    wants one sets it explicitly; a test that doesn't should see none.
    """
    for attribute in ("memory", "rag", "web"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)


@pytest.fixture
async def client(provider: FakeProvider):
    """A client with no database wired in, for tests that don't need one."""
    app.state.settings = FakeSettings()
    app.state.provider = provider
    app.state.http = httpx.AsyncClient(timeout=1.0)
    # No factory: health should report the database as unreachable, not crash.
    app.state.session_factory = None
    _reset_optional_clients()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.http.aclose()


@pytest.fixture
async def db_client(provider: FakeProvider, session_factory):
    """A client backed by the real database, rolled back after each test.

    Pre-authenticated as the seeded demo user (supabase/seed.sql) — every
    protected route now requires a real session, and existing tests were
    written assuming DEMO_USER_ID without ever having to think about login.
    Rather than rewrite every one of them to sign up first, the fixture logs
    them in for free: it crafts a valid session token directly (same
    function auth.py itself uses), skipping the signup endpoint and its
    password hashing entirely — there's nothing about the login *flow* being
    tested by paying that cost in every unrelated test.
    """
    settings = FakeSettings()
    app.state.settings = settings
    app.state.provider = provider
    app.state.http = httpx.AsyncClient(timeout=1.0)
    app.state.session_factory = session_factory
    _reset_optional_clients()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_session_token(DEMO_USER_ID, settings)
        c.cookies.set(SESSION_COOKIE_NAME, token)
        yield c

    await app.state.http.aclose()
