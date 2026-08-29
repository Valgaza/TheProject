"""Nexus core service — chat, orchestration, and the model provider layer.

Talks to the memory service (port 8000) over HTTP. Owns no graph of its own.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import Settings
from .logging_config import configure_logging
from .providers import Provider, build_provider

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    provider: str
    chat_model: str
    memory_service: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    settings = Settings()
    provider = build_provider(settings)
    http = httpx.AsyncClient(timeout=10.0)

    app.state.settings = settings
    app.state.provider = provider
    app.state.http = http

    logger.info(
        "Core service ready  provider=%s chat_model=%s memory=%s",
        provider.name,
        settings.chat_model,
        settings.memory_service_url,
    )
    yield

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
)


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

    return HealthResponse(
        status="ok" if memory_status == "ok" else "degraded",
        provider=provider.name,
        chat_model=settings.chat_model,
        memory_service=memory_status,
    )
