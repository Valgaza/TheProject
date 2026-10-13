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
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id
from ..chat_service import FAILED_ANSWER, PreparedTurn, finalize_turn, prepare_turn
from ..db import session_dependency
from ..decisions import runtime_root
from ..schemas import MessageOut
from ..voice.runtime import RuntimeVoice
from .chats import _get_chat, _live, _queue_for_memory, _turn_kwargs, _wake_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


@dataclass
class _Handle:
    session: AsyncSession
    prepared: PreparedTurn
    error: str | None = None
    first_token_ms: int | None = None
    started: float = field(default_factory=time.perf_counter)


class ChatTurns:
    """A spoken turn, in the three steps voice holds apart, on the same
    functions as typed chat: store the message and gather memory, documents
    and the web; stream the answer; store what was said (only what was heard,
    if interrupted) and queue the turn for memory."""

    def __init__(self, app, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.app = app
        self._holder = SimpleNamespace(app=app)  # what the chat helpers read: only .app
        self.chat_id = chat_id
        self.user_id = user_id

    async def prepare(self, text: str):
        session = self.app.state.session_factory()
        try:
            chat = await _get_chat(session, self.chat_id, self.user_id)
            prepared = await prepare_turn(
                session, **_turn_kwargs(self._holder, chat, text, False, self.user_id), spoken=True)
        except BaseException:
            await session.close()
            raise
        shown = {
            "user_message": MessageOut.model_validate(prepared.user_message).model_dump(mode="json", by_alias=True),
            "route": {"handler": prepared.route.handler, "reason": prepared.route.reason},
            "recalled": [{"id": f.id, "fact": f.fact, "scope": f.scope, "kind": f.kind} for f in prepared.facts],
            "rag_sources": [{"file_name": s.file_name, "page_number": s.page_number,
                             "chunk_index": s.chunk_index, "heading_path": s.heading_path,
                             "rerank_score": round(s.rerank_score, 4)}
                            for s in (prepared.rag_result.sources if prepared.rag_result else ())],
        }
        return _Handle(session=session, prepared=prepared), shown

    async def stream(self, handle: _Handle):
        handle.started = time.perf_counter()
        try:
            async for piece in self.app.state.provider.chat_stream(handle.prepared.prompt, priority="live"):
                if piece and handle.first_token_ms is None:
                    handle.first_token_ms = round((time.perf_counter() - handle.prepared.started) * 1000)
                yield piece
        except Exception as exc:
            logger.exception("voice generation failed for chat=%s", self.chat_id)
            handle.error = f"{exc.__class__.__name__}: {exc}"
            if handle.first_token_ms is None:
                yield FAILED_ANSWER

    async def finish(self, handle: _Handle, *, answer: str, interrupted: bool, voice: dict) -> dict:
        session = handle.session
        try:
            turn = await finalize_turn(
                session, handle.prepared, answer=answer, error=handle.error,
                generation_ms=round((time.perf_counter() - handle.started) * 1000),
                first_token_ms=handle.first_token_ms, interrupted=interrupted,
                extra_meta={"voice": voice},
            )
            # Queued even when interrupted: what the person said still counts.
            _queue_for_memory(self._holder, session, handle.prepared.chat, turn, self.user_id)
            await session.commit()
        finally:
            await session.close()
        _wake_queue(self._holder)
        return {"assistant_message": MessageOut.model_validate(turn.assistant_message)
                .model_dump(mode="json", by_alias=True), "chat_title": handle.prepared.chat.title}


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
        speech = getattr(app.state, "voice_speech", None) or RuntimeVoice(
            app.state.http, runtime_root(app.state.settings.openai_base_url))
        task = asyncio.create_task(run_session(
            connection, speech=speech, turns=ChatTurns(app, chat_id, user_id),
            live=_live(SimpleNamespace(app=app)), chat_id=chat_id,
            settings=_voice_settings(app.state.settings)))
        app.state.voice_sessions.add(task)
        task.add_done_callback(app.state.voice_sessions.discard)

    def request_for() -> SmallWebRTCRequest:
        return SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc)

    try:
        answer = await rtc.handle_web_request(request_for(), start)
    except HTTPException:
        raise
    except Exception:
        logger.exception("voice offer failed; retrying with a new WebRTC handler")
        # A new session never needs the old handler's connections, so a
        # handler left in a bad state is replaced rather than kept failing.
        # An unhandled error would also reach the browser without CORS
        # headers, as a bare "Failed to fetch".
        if body.pc_id:
            raise HTTPException(status_code=409, detail="voice connection lost; start voice again")
        request.app.state.webrtc = None
        rtc = _rtc(request)
        owners = app.state.webrtc_owners
        try:
            answer = await rtc.handle_web_request(request_for(), start)
        except Exception as exc:
            logger.exception("voice offer failed again")
            raise HTTPException(status_code=503, detail=f"Voice could not start: {exc}") from exc
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
