"""The graph panel's door into memory.

The browser used to call the memory service itself, naming the user in the
URL, and memory had no way to check it. Now the panel asks core, core takes
the user from the session cookie, and memory only answers core (see
memory/nexus_memory/service_auth.py). The id in the upstream call is always
the session's, never one the browser supplied.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Literal

import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..auth import SESSION_COOKIE_NAME, current_user_id, decode_session_token
from ..db import session_dependency
from ..memory_client import MemoryClient
from ..models import Project

logger = logging.getLogger(__name__)

router = APIRouter(tags=["graph"])

View = Literal["all", "user", "project"]


def _memory(app) -> MemoryClient:
    memory: MemoryClient | None = getattr(app.state, "memory", None)
    if memory is None:
        raise HTTPException(status_code=503, detail="memory service is not configured")
    return memory


async def _check_view(
    session: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID | None, view: str
) -> None:
    """A project view needs a project, and it has to be this user's.

    404 rather than 403 for someone else's project, as everywhere else: the
    answer to "may I see project X" should not confirm that X exists.
    """
    if view == "project" and project_id is None:
        raise HTTPException(status_code=422, detail="the project view requires a project_id")
    if project_id is not None:
        project = await session.get(Project, project_id)
        if project is None or project.user_id != user_id:
            raise HTTPException(status_code=404, detail="project not found")


@router.get("/graph")
async def get_graph(
    request: Request,
    view: View = "all",
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> dict:
    user_id = current_user_id(request)
    await _check_view(session, user_id, project_id, view)
    try:
        return await _memory(request.app).graph(user_id=user_id, project_id=project_id, view=view)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Graph read failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"memory service: {exc}") from exc


@router.delete("/graph", status_code=204)
async def delete_graph(
    request: Request,
    view: View = "all",
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> None:
    user_id = current_user_id(request)
    await _check_view(session, user_id, project_id, view)
    try:
        await _memory(request.app).delete_graph(user_id=user_id, project_id=project_id, view=view)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Graph delete failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"memory service: {exc}") from exc


@router.websocket("/ws/graph")
async def ws_graph(websocket: WebSocket, view: View = "all", project_id: uuid.UUID | None = None):
    """Relay memory's graph socket, after checking who is asking.

    Browsers send cookies on a websocket handshake but apply no CORS to it,
    so the Origin check here is what stops another site open in the same
    browser from subscribing with this user's cookie.
    """
    app = websocket.app
    settings = app.state.settings

    if websocket.headers.get("origin") not in settings.cors_origins:
        await websocket.close(code=1008, reason="origin not allowed")
        return
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    user_id = decode_session_token(token, settings) if token else None
    if user_id is None:
        await websocket.close(code=1008, reason="not logged in")
        return

    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    try:
        async with factory() as session:
            await _check_view(session, user_id, project_id, view)
        memory = _memory(app)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail))
        return

    url = memory.graph_socket_url(user_id=user_id, project_id=project_id, view=view)
    try:
        upstream = await websockets.connect(
            url, additional_headers={"X-Service-Token": settings.service_token}
        )
    except Exception as exc:
        logger.warning("Graph socket upstream failed: %s", exc)
        await websocket.close(code=1011, reason="memory service unreachable")
        return

    await websocket.accept()

    async def downstream() -> None:
        async for frame in upstream:
            await websocket.send_text(frame if isinstance(frame, str) else frame.decode())

    async def until_client_leaves() -> None:
        # Inbound frames are not part of the protocol; this only waits for the
        # browser to go away so the upstream socket is not left open behind it.
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    relay = asyncio.create_task(downstream())
    watch = asyncio.create_task(until_client_leaves())
    try:
        await asyncio.wait([relay, watch], return_when=asyncio.FIRST_COMPLETED)
        client_left = watch.done()
    finally:
        relay.cancel()
        watch.cancel()
        await upstream.close()
    if not client_left:
        # Memory went away first: tell the browser, so the panel can reconnect
        # instead of sitting on a socket that will never speak again.
        try:
            await websocket.close(code=1011, reason="memory service closed the stream")
        except RuntimeError:
            pass  # the browser left in the same moment
