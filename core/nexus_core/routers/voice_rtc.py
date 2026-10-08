"""Talking to a chat over WebRTC: POST/PATCH /voice/{chat_id}/offer.

The browser's Pipecat client (frontend/src/lib/voice.ts) sends its WebRTC
offer here and gets the answer back; after that, microphone audio and the
spoken answer travel over the peer connection, and transcripts, the streamed
answer and the stored messages arrive as RTVI server messages
(voice/pipeline.py). PATCH carries ICE candidates for a connection already
made.

The browser still talks only to core: the session cookie is checked here,
and the chat must be the caller's. A connection id is only good for the
person who opened it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id
from ..db import session_dependency
from ..decisions import runtime_root
from .chats import _get_chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


class Offer(BaseModel):
    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool | None = None
    request_data: Any | None = Field(default=None, alias="requestData")


class Candidate(BaseModel):
    candidate: str = ""
    sdp_mid: str | None = None
    sdp_mline_index: int | None = None


class CandidatePatch(BaseModel):
    pc_id: str
    candidates: list[Candidate]


def _rtc(request: Request):
    """One request handler for the app: it holds the open peer connections."""
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

    state = request.app.state
    if getattr(state, "webrtc", None) is None:
        state.webrtc = SmallWebRTCRequestHandler()
        state.webrtc_owners = {}
        state.voice_sessions = set()
    return state.webrtc


def _check_origin(request: Request) -> None:
    # CORS already keeps other sites from reading the answer; refusing them
    # outright means they cannot open a session on this person's cookie at all.
    origin = request.headers.get("origin")
    if origin is not None and origin not in request.app.state.settings.cors_origins:
        raise HTTPException(status_code=403, detail="origin not allowed")


def _voice_settings(settings):
    from ..voice.pipeline import VoiceSettings

    defaults = VoiceSettings()
    return VoiceSettings(
        vad_stop_ms=getattr(settings, "voice_vad_stop_ms", defaults.vad_stop_ms),
        incomplete_wait_ms=getattr(settings, "voice_incomplete_wait_ms", defaults.incomplete_wait_ms),
        interrupt_min_words=getattr(settings, "voice_interrupt_min_words", defaults.interrupt_min_words),
        interrupt_after_ms=getattr(settings, "voice_interrupt_after_ms", defaults.interrupt_after_ms),
        continue_within_ms=getattr(settings, "voice_continue_within_ms", defaults.continue_within_ms),
    )


@router.post("/{chat_id}/offer")
async def offer(
    chat_id: uuid.UUID,
    body: Offer,
    request: Request,
    # Before the session: a request with no login never touches the database.
    user_id: uuid.UUID = Depends(current_user_id),
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> dict:
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

    from ..voice.pipeline import run_session

    _check_origin(request)
    await _get_chat(session, chat_id, user_id)
    app = request.app
    if runtime_root(getattr(app.state.settings, "openai_base_url", None)) is None:
        raise HTTPException(status_code=503, detail="voice needs the MLX runtime")
    rtc = _rtc(request)
    owners: dict[str, uuid.UUID] = app.state.webrtc_owners
    if body.pc_id and owners.get(body.pc_id, user_id) != user_id:
        raise HTTPException(status_code=404, detail="connection not found")

    async def start(connection) -> None:
        @connection.event_handler("closed")
        async def forget(conn) -> None:
            owners.pop(conn.pc_id, None)

        # In the background: the browser is waiting for this request's answer
        # before the connection can carry any audio.
        task = asyncio.create_task(run_session(
            connection, app=app, chat_id=chat_id, user_id=user_id,
            settings=_voice_settings(app.state.settings)))
        app.state.voice_sessions.add(task)
        task.add_done_callback(app.state.voice_sessions.discard)

    answer = await rtc.handle_web_request(
        SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc),
        start,
    )
    owners[answer["pc_id"]] = user_id
    return answer


@router.patch("/{chat_id}/offer")
async def candidates(chat_id: uuid.UUID, body: CandidatePatch, request: Request) -> dict:
    from pipecat.transports.smallwebrtc.request_handler import IceCandidate, SmallWebRTCPatchRequest

    _check_origin(request)
    user_id = current_user_id(request)
    rtc = _rtc(request)
    if request.app.state.webrtc_owners.get(body.pc_id) != user_id:
        raise HTTPException(status_code=404, detail="connection not found")
    await rtc.handle_patch_request(SmallWebRTCPatchRequest(
        pc_id=body.pc_id,
        candidates=[IceCandidate(candidate=c.candidate, sdp_mid=c.sdp_mid or "",
                                 sdp_mline_index=c.sdp_mline_index or 0) for c in body.candidates],
    ))
    return {"status": "ok"}


async def close_voice(app) -> None:
    """At shutdown: hang up every open voice session."""
    rtc = getattr(app.state, "webrtc", None)
    if rtc is None:
        return
    for task in list(getattr(app.state, "voice_sessions", ())):
        task.cancel()
    await rtc.close()
