"""Talking to a chat: WS /ws/voice/{chat_id}.

The browser talks only to core, as for the graph socket: core checks the
session cookie and the Origin, owns the conversation (voice/session.py), and
reaches the speech models in the runtime with the service token.

Protocol. Browser -> core:
  binary          microphone audio, PCM16 mono at 16 kHz, any chunk size
  {"type": "playing", "turn": t, "index": i}   started playing sentence i
  {"type": "played",  "turn": t, "index": i}   finished playing it
  {"type": "interrupt"}                        the stop button

Core -> browser:
  {"type": "loading"} then {"type": "ready", "sample_rate": 16000}
  {"type": "vad", "speaking": bool}            the person started/stopped
  {"type": "transcript", "turn", "text"}       what they said, final
  {"type": "prepared", "turn", route, recalled, rag_sources, user_message}
  {"type": "token", "turn", "text"}            the answer as it is written
  {"type": "audio", "turn", "index", "text", "sample_rate": 24000, "seconds"}
      followed by one binary frame: that sentence, PCM16 mono
  {"type": "pause"} / {"type": "resume"}       speech over the assistant: hold
                                               playback while it is judged
  {"type": "interrupted", "turn"}              stop playback, drop the queue
  {"type": "done", "turn", assistant_message}  stored
  {"type": "error", "detail"}

Every turn is an ordinary message in the chat (meta.voice holds its timings),
so the transcript reads the same afterwards as a typed conversation. While a
session is open the memory write queue waits, as it does for a typed answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import SESSION_COOKIE_NAME, decode_session_token
from ..chat_service import (FAILED_ANSWER, PreparedTurn, Retrieval, finalize_turn, prepare_turn,
                            start_retrieval)
from ..decisions import Decision, log_entry, runtime_root
from ..schemas import MessageOut
from ..voice.runtime import RuntimeVoice
from ..voice.session import VoiceConfig, VoiceSession
from ..voice.vad import SAMPLE_RATE, make_vad
from .chats import _get_chat, _live, _queue_for_memory, _turn_kwargs, _wake_queue

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


@dataclass
class _Handle:
    session: AsyncSession
    prepared: PreparedTurn
    error: str | None = None
    first_token_ms: int | None = None
    started: float = field(default_factory=time.perf_counter)


class ChatTurns:
    """voice/session.py's TurnBackend, on the same functions as typed chat."""

    def __init__(self, websocket: WebSocket, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.ws = websocket
        self.app = websocket.app
        self.chat_id = chat_id
        self.user_id = user_id

    async def prefetch(self, text: str) -> Retrieval:
        async with self.app.state.session_factory() as session:
            chat = await _get_chat(session, self.chat_id, self.user_id)
        state = self.app.state
        return start_retrieval(content=text, chat=chat, user_id=self.user_id,
                               memory=getattr(state, "memory", None), rag=getattr(state, "rag", None),
                               rag_min_score=state.settings.rag_min_score)

    def discard(self, prefetched: Retrieval) -> None:
        prefetched.discard()

    async def prepare(self, text: str, prefetched: Retrieval | None = None):
        session = self.app.state.session_factory()
        try:
            chat = await _get_chat(session, self.chat_id, self.user_id)
            prepared = await prepare_turn(session, **_turn_kwargs(self.ws, chat, text, False, self.user_id),
                                          spoken=True, retrieval=prefetched)
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
            _queue_for_memory(self.ws, session, handle.prepared.chat, turn, self.user_id)
            await session.commit()
        finally:
            await session.close()
        _wake_queue(self.ws)
        return {"assistant_message": MessageOut.model_validate(turn.assistant_message)
                .model_dump(mode="json", by_alias=True), "chat_title": handle.prepared.chat.title}

    async def log_decision(self, point: str, mode: str, state: dict,
                           decision: Decision | None, baseline: dict) -> None:
        try:
            async with self.app.state.session_factory() as session:
                session.add(log_entry(point=point, mode=mode, state=state, decision=decision,
                                      baseline=baseline, chat_id=self.chat_id))
                await session.commit()
        except Exception:
            logger.exception("could not log the %s decision", point)


def _config(settings) -> VoiceConfig:
    return VoiceConfig(
        speech_threshold=settings.voice_speech_threshold,
        barge_threshold=settings.voice_barge_threshold,
        eot_silence_ms=settings.voice_eot_silence_ms,
        speculate_silence_ms=getattr(settings, "voice_speculate_silence_ms", 250),
        max_silence_ms=settings.voice_max_silence_ms,
        barge_in_ms=settings.voice_barge_in_ms,
        laya_end_of_turn=settings.laya_end_of_turn,
        laya_barge_in=settings.laya_barge_in,
    )


@router.websocket("/ws/voice/{chat_id}")
async def ws_voice(websocket: WebSocket, chat_id: uuid.UUID):
    app = websocket.app
    settings = app.state.settings

    # Browsers apply no CORS to a websocket handshake but do send cookies:
    # the Origin check is what stops another site using this person's session.
    if websocket.headers.get("origin") not in settings.cors_origins:
        await websocket.close(code=1008, reason="origin not allowed")
        return
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    user_id = decode_session_token(token, settings) if token else None
    if user_id is None:
        await websocket.close(code=1008, reason="not logged in")
        return
    try:
        async with app.state.session_factory() as session:
            await _get_chat(session, chat_id, user_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail))
        return
    root = runtime_root(settings.openai_base_url)
    if root is None:
        await websocket.close(code=1011, reason="voice needs the MLX runtime")
        return

    await websocket.accept()
    speech = getattr(app.state, "voice_speech", None) or RuntimeVoice(app.state.http, root)
    send_lock = asyncio.Lock()

    async def send(message) -> None:
        # One lock, so an audio header and its binary frame are never split
        # by another message.
        async with send_lock:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(json.dumps(message, default=str))

    voice = VoiceSession(
        send=send, speech=speech, vad=make_vad(), turns=ChatTurns(websocket, chat_id, user_id),
        config=_config(settings), decisions=getattr(app.state, "decisions", None),
    )
    async with _live(websocket):
        try:
            await send({"type": "loading"})
            # The only model load voice ever waits for, and it is here, before
            # the first word -- never inside a turn.
            await speech.preload()
            await send({"type": "ready", "sample_rate": SAMPLE_RATE})
            while True:
                frame = await websocket.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    await voice.feed_audio(frame["bytes"])
                elif frame.get("text"):
                    try:
                        await voice.on_message(json.loads(frame["text"]))
                    except (ValueError, TypeError):
                        await send({"type": "error", "detail": "unreadable message"})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("voice session failed for chat=%s", chat_id)
        finally:
            await voice.close()
