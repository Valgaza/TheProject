"""The runtime service: every local model, on MLX, behind one API.

    uv run uvicorn nexus_runtime.main:app --port 8003

Models load in the background after startup, so /health answers immediately
and says "loading" until the chat and embedding models are in memory and
warmed. start.sh waits for "ok" before starting anything that calls this.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .config import Settings
from .engine import Scheduler
from .mlx_backend import MLXBackend
from .service_auth import ServiceTokenMiddleware

logger = logging.getLogger("nexus_runtime")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    settings = Settings()
    scheduler = Scheduler()
    scheduler.start()
    backend = MLXBackend(settings, scheduler)
    app.state.settings = settings
    app.state.backend = backend

    async def warm_up() -> None:
        try:
            await backend.start()
        except Exception:
            # Visible in /health as "loading" forever, and here with the cause.
            logger.exception("runtime failed to load its models")

    task = asyncio.create_task(warm_up())
    try:
        yield
    finally:
        task.cancel()
        scheduler.stop()


app = FastAPI(title="Nexus Runtime", version="0.1.0", lifespan=lifespan)
app.add_middleware(ServiceTokenMiddleware)
app.include_router(router)
