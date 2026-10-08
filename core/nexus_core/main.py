"""Nexus core service — chat, orchestration, and the model provider layer.

Talks to the memory service (port 8000) over HTTP. Owns no graph of its own.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings
from .db import create_engine, create_session_factory
from .logging_config import configure_logging
from .memory_client import MemoryClient
from .providers import Provider, build_provider
from .rag_client import RagClient
from .web_client import WebClient
from .write_queue import Activity, WriteQueue
from .decisions import DecisionsClient, runtime_root
from .routers.voice_rtc import close_voice
from .routers import (
    auth_router,
    chats_router,
    documents_router,
    graph_router,
    maintenance_router,
    projects_router,
    voice_router,
    voice_rtc_router,
)

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    provider: str
    chat_model: str
    memory_service: str
    rag_service: str
    web_service: str
    database: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    settings = Settings()
    provider = build_provider(settings)
    # Only ever pointed at memory and RAG, which refuse anything without this
    # header. Google OAuth goes through Authlib's own client, not this one, so
    # the token never leaves the machine.
    http = httpx.AsyncClient(
        timeout=10.0, headers={"X-Service-Token": settings.service_token}
    )
    engine = create_engine(settings.database_url, echo=settings.database_echo)

    app.state.settings = settings
    app.state.provider = provider
    app.state.memory = MemoryClient(http, settings.memory_service_url)
    # Read by the chat router and handed to generate_turn, which queries it
    # on every turn; CORE_RAG_MIN_SCORE decides whether the result reaches
    # the prompt. Optional throughout: a failed query degrades the turn.
    app.state.rag = RagClient(http, settings.rag_service_url)
    app.state.http = http
    app.state.web = WebClient(http, settings.websearch_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    # Google OAuth is registered here, not at module level, because it needs
    # settings — but only actually registered if a client id/secret exist,
    # so a deployment running email/password only never touches Google at
    # all, not even to fail at startup.
    oauth = OAuth()
    if settings.google_oauth_enabled:
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    app.state.oauth = oauth

    # Live work (a reply being generated, later a voice session) registers
    # with this; the memory write queue waits for it to be quiet.
    activity = Activity()
    app.state.activity = activity
    # The decision model lives in the runtime; with inference elsewhere
    # (the Ollama fallback) there is none, and every point keeps its old logic.
    root = runtime_root(settings.openai_base_url)
    decisions = DecisionsClient(http, root) if root else None
    app.state.decisions = decisions
    write_queue = WriteQueue(
        session_factory=app.state.session_factory,
        provider=provider,
        memory=app.state.memory,
        gate_model=settings.gate_model,
        activity=activity,
        decisions=decisions,
        laya_gate=settings.laya_gate,
        laya_skip_below=settings.laya_gate_skip_below,
    )
    write_queue.start()
    app.state.write_queue = write_queue

    logger.info(
        "Core service ready  provider=%s chat_model=%s memory=%s google_oauth=%s",
        provider.name,
        settings.chat_model,
        settings.memory_service_url,
        settings.google_oauth_enabled,
    )
    yield

    await close_voice(app)
    await write_queue.stop()
    await engine.dispose()
    await http.aclose()
    aclose = getattr(provider, "aclose", None)
    if aclose is not None:
        await aclose()
    logger.info("Core service shut down")


app = FastAPI(title="Nexus Core Service", version="0.1.0", lifespan=lifespan)

# CORS has to be registered before startup, so origins are read here rather than
# from Settings (which requires an API key and is built inside the lifespan).
_cors_origins = [
    origin.strip()
    for origin in os.getenv("CORE_CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
    # Required for the session cookie to travel at all: the frontend (:3000)
    # and core (:8100) are different origins, and a browser drops cookies on
    # a cross-origin request unless the server explicitly opts in here. The
    # CORS spec forbids combining this with allow_origins=["*"] — which is
    # exactly why _cors_origins was already an explicit list, not "*".
    allow_credentials=True,
)

# Also registered before startup, same reason as CORS — Authlib's OAuth
# client needs somewhere to stash a short-lived nonce between "redirect to
# Google" and "Google redirects back", and Starlette's SessionMiddleware is
# that somewhere. Separate from our own session cookie (auth.py) entirely:
# this one is Authlib's internal bookkeeping, ours is what identifies a
# logged-in user.
_jwt_secret = os.getenv("JWT_SECRET")
if _jwt_secret:
    app.add_middleware(SessionMiddleware, secret_key=_jwt_secret)

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(chats_router)
app.include_router(documents_router)
app.include_router(graph_router)
app.include_router(voice_router)
app.include_router(voice_rtc_router)
app.include_router(maintenance_router)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_provider(request: Request) -> Provider:
    return request.app.state.provider


@app.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness plus whether the memory service is reachable.

    Always 200, like the memory service's own health endpoint; read the fields
    rather than the status code.
    """
    settings: Settings = request.app.state.settings
    provider: Provider = request.app.state.provider
    http: httpx.AsyncClient = request.app.state.http

    try:
        response = await http.get(f"{settings.memory_service_url}/health", timeout=3.0)
        memory_status = "ok" if response.status_code == 200 else "degraded"
    except Exception as exc:
        logger.warning("Memory service unreachable: %s", exc)
        memory_status = "unreachable"

    try:
        response = await http.get(f"{settings.rag_service_url}/api/health", timeout=3.0)
        rag_status = "ok" if response.status_code == 200 else "degraded"
    except Exception as exc:
        logger.warning("RAG service unreachable: %s", exc)
        rag_status = "unreachable"

    try:
        # The websearch service reports SearXNG's state too: "degraded" when
        # the service is up but the search engine behind it is not.
        response = await http.get(f"{settings.websearch_url}/health", timeout=3.0)
        searxng = response.json().get("searxng") if response.status_code == 200 else None
        web_status = "ok" if searxng == "ok" else "degraded"
    except Exception as exc:
        logger.warning("Web search service unreachable: %s", exc)
        web_status = "unreachable"

    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("select 1"))
        database_status = "ok"
    except Exception as exc:
        logger.error("Database unreachable: %s", exc)
        database_status = "unreachable"

    # RAG and web search aren't required the way memory/database are; both
    # are optional everywhere else, so neither affects overall `status`.
    healthy = memory_status == "ok" and database_status == "ok"
    return HealthResponse(
        status="ok" if healthy else "degraded",
        provider=provider.name,
        chat_model=settings.chat_model,
        memory_service=memory_status,
        rag_service=rag_status,
        web_service=web_status,
        database=database_status,
    )
