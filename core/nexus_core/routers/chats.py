"""Chats and their messages.

Posting a message runs a full turn: route it, store it, answer it, store the
answer. Retrieval joins that sequence in phase 4.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import nullcontext

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id as _current_user_id
from ..chat_service import Turn, generate_turn
from ..db import session_dependency
from ..models import Chat, MemoryEpisode, Message, Project
from ..memory_client import MemoryClient
from ..memory_writer import enqueue_turn
from ..providers.base import Provider
from ..rag_client import RagClient
from ..schemas import (
    ChatCreate,
    ChatOut,
    ChatUpdate,
    FactOut,
    MemoryEpisodeOut,
    MessageCreate,
    MessageOut,
    RagSourceOut,
    RouteOut,
    TurnOut,
)

router = APIRouter(prefix="/chats", tags=["chats"])


async def _get_chat(session: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID) -> Chat:
    """Load a chat, refusing one that belongs to somebody else.

    Checks Chat.user_id directly rather than joining through Project --
    that join used to be the only way to know who a chat belonged to, but
    it silently excluded unfiled chats (project_id IS NULL) entirely,
    which would have made every one of them permanently unreachable.
    """
    result = await session.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _provider(request: Request) -> Provider:
    return request.app.state.provider


def _memory(request: Request) -> MemoryClient | None:
    return getattr(request.app.state, "memory", None)


def _rag(request: Request) -> RagClient | None:
    return getattr(request.app.state, "rag", None)


def _settings(request: Request):
    return request.app.state.settings


@router.get("", response_model=list[ChatOut])
async def list_chats(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
    loose: bool = False,
) -> list[Chat]:
    """Every chat this user has, newest activity first.

    ``loose=true`` narrows it to the ones filed under no project. The sidebar
    asks for both: the full list to nest under project headings, and the loose
    ones for the plain list underneath.
    """
    query = select(Chat).where(Chat.user_id == _current_user_id(request))
    if loose:
        query = query.where(Chat.project_id.is_(None))
    result = await session.execute(query.order_by(Chat.updated_at.desc()))
    return list(result.scalars())


@router.post("", response_model=ChatOut, status_code=201)
async def create_chat(
    body: ChatCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Chat:
    """Start a conversation, with or without a project.

    POST /projects/{id}/chats is the other door in; this one exists so a chat
    can be started with no project at all, which the nested route cannot express.
    """
    user_id = _current_user_id(request)
    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None or project.user_id != user_id:
            raise HTTPException(status_code=404, detail="project not found")
    chat = Chat(user_id=user_id, project_id=body.project_id, title=body.title)
    session.add(chat)
    await session.flush()
    return chat


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(
    chat_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Chat:
    return await _get_chat(session, chat_id, _current_user_id(request))


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(
    chat_id: uuid.UUID,
    body: ChatUpdate,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Chat:
    """Rename, pin/unpin, or move to a different project -- whichever
    fields are set. Covers the sidebar's three-dot menu."""
    if body.title is None and body.pinned is None and body.project_id is None:
        raise HTTPException(status_code=422, detail="nothing to update")

    user_id = _current_user_id(request)
    chat = await _get_chat(session, chat_id, user_id)

    if body.project_id is not None and body.project_id != chat.project_id:
        # Moving to a project the caller doesn't own would either 404 (it
        # doesn't exist for them) or silently hand the chat to someone
        # else's project -- reuse the same ownership check project reads
        # already go through, not a bespoke one.
        result = await session.execute(
            select(Project).where(Project.id == body.project_id, Project.user_id == user_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="project not found")
        chat.project_id = body.project_id

    if body.title is not None:
        chat.title = body.title
    if body.pinned is not None:
        chat.pinned = body.pinned

    await session.flush()
    return chat


@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> None:
    """Removes the chat and its transcript. Anything the write gate already
    extracted into the knowledge graph survives -- Postgres holds what was
    said, Neo4j holds what was learned, deliberately separate stores, and
    deleting a conversation shouldn't unlearn a fact that came from it."""
    chat = await _get_chat(session, chat_id, _current_user_id(request))
    await session.delete(chat)


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(
    chat_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> list[Message]:
    await _get_chat(session, chat_id, _current_user_id(request))
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars())


logger = logging.getLogger(__name__)


def _turn_kwargs(request: Request, chat: Chat, body: MessageCreate, user_id: uuid.UUID) -> dict:
    return dict(
        chat=chat,
        content=body.content,
        provider=_provider(request),
        user_id=user_id,
        memory=_memory(request),
        rag=_rag(request),
        title_model=request.app.state.settings.gate_model,
        rag_min_score=_settings(request).rag_min_score,
        web=getattr(request.app.state, "web", None),
        use_web=body.use_web,
    )


def _live(request: Request):
    """Mark this request as live work, so the memory write queue waits."""
    activity = getattr(request.app.state, "activity", None)
    return activity.live() if activity is not None else nullcontext()


def _queue_for_memory(request: Request, session: AsyncSession, chat: Chat, turn: Turn, user_id) -> None:
    """Queue the finished turn for the write gate, in the turn's transaction.

    Memory forms after the answer, never in front of it: the gate and
    graphiti's extraction run on the write queue (write_queue.py), which
    only starts them once nothing live is happening.
    """
    if _memory(request) is None:
        return
    enqueue_turn(
        session,
        chat_id=chat.id,
        project_id=chat.project_id,
        user_id=user_id,
        message_id=turn.user_message.id,
        user_message=turn.user_message.content,
        assistant_message=turn.assistant_message.content,
        # What memory already supplied for this turn. The gate refuses to
        # restate any of it, which is what stops recall from making a fact
        # louder every time it is recalled.
        recalled_facts=[f.fact for f in turn.facts],
    )


def _wake_queue(request: Request) -> None:
    queue = getattr(request.app.state, "write_queue", None)
    if queue is not None:
        queue.wake()


def _turn_out(turn: Turn, chat: Chat) -> TurnOut:
    return TurnOut(
        user_message=MessageOut.model_validate(turn.user_message),
        assistant_message=MessageOut.model_validate(turn.assistant_message),
        route=RouteOut(handler=turn.route.handler, reason=turn.route.reason),
        chat_title=chat.title,
        recalled=[
            FactOut(id=f.id, fact=f.fact, scope=f.scope, kind=f.kind)
            for f in turn.facts
        ],
        rag_sources=[
            RagSourceOut(
                file_name=s.file_name,
                page_number=s.page_number,
                chunk_index=s.chunk_index,
                heading_path=s.heading_path,
                rerank_score=round(s.rerank_score, 4),
            )
            for s in turn.rag_sources
        ],
    )


def _validate_post(body: MessageCreate) -> None:
    if body.role != "user":
        raise HTTPException(
            status_code=422,
            detail="only user messages can be posted; the assistant turn is generated",
        )


@router.post("/{chat_id}/messages", response_model=TurnOut, status_code=201)
async def create_message(
    chat_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> TurnOut:
    """Send a message and get the answer to it.

    The memory write that follows is queued, not run here: graphiti's
    extraction takes seconds and nobody should wait on it to read their reply.
    """
    _validate_post(body)
    user_id = _current_user_id(request)
    chat = await _get_chat(session, chat_id, user_id)
    async with _live(request):
        turn = await generate_turn(session, **_turn_kwargs(request, chat, body, user_id))
    _queue_for_memory(request, session, chat, turn, user_id)
    await session.commit()
    _wake_queue(request)
    return _turn_out(turn, chat)


@router.get("/{chat_id}/memory-episodes", response_model=list[MemoryEpisodeOut])
async def list_memory_episodes(
    chat_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> list[MemoryEpisode]:
    """What this chat put into memory, and what happened to it.

    Only persisted facts appear. The gate declines most turns by design, and
    those are logged rather than stored -- a row per "thanks" would bury the
    rows that matter.
    """
    await _get_chat(session, chat_id, _current_user_id(request))
    result = await session.execute(
        select(MemoryEpisode)
        .where(MemoryEpisode.chat_id == chat_id)
        .order_by(MemoryEpisode.created_at)
    )
    return list(result.scalars())
