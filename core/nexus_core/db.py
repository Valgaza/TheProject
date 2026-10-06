"""Database engine and session handling.

``core`` talks to Postgres directly over asyncpg rather than through
``supabase-py``: the Supabase client libraries are built for browser use, and
server-side code doing real queries wants SQL and a connection pool.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import Request

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Rewrite a stock Postgres URL to the asyncpg driver.

    Supabase hands out ``postgresql://`` (and the dashboard sometimes
    ``postgres://``); SQLAlchemy needs the driver named explicitly.
    """
    for prefix in ("postgresql+asyncpg://", "sqlite+aiosqlite://"):
        if url.startswith(prefix):
            return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build the engine, with prepared statements disabled on Postgres.

    Supabase's transaction-mode pooler (port 6543) multiplexes connections, so a
    prepared statement created on one backend can be looked up on another and
    fail. Turning both caches off is the supported way to run asyncpg behind it,
    and costs little at this scale.
    """
    normalized = normalize_database_url(url)
    kwargs: dict = {"echo": echo, "pool_pre_ping": True}

    if normalized.startswith("postgresql+asyncpg://"):
        kwargs["connect_args"] = {"statement_cache_size": 0}
        kwargs["execution_options"] = {"compiled_cache": None}

    return create_async_engine(normalized, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_dependency(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that commits on success.

    Always injected with ``scope="function"``: FastAPI's default for a yield
    dependency is "request", which runs the commit *after* the response is
    sent. A client that used what it had just created (send a message to the
    chat it just made) could then arrive before the row was committed and get
    a 404 -- intermittently, which is how the scenario suite found it.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
