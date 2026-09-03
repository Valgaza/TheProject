"""Chats and their messages.

Posting a message runs a full turn: route it, store it, answer it, store the
answer. Retrieval joins that sequence in phase 4.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id as _current_user_id
from ..chat_service import generate_turn
from ..db import session_dependency
from ..models import Chat, MemoryEpisode, Message, Project
from ..memory_client import MemoryClient
from ..memory_writer import run_write_gate
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


@router.get("", response_model=list[ChatOut])
async def list_unfiled_chats(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> list[Chat]:
    """Chats not filed under any project -- created from /chat directly.
    The sidebar merges these with the current project's own chats
    (GET /projects/{id}/chats) rather than showing them separately."""
    result = await session.execute(
        select(Chat)
        .where(Chat.project_id.is_(None), Chat.user_id == _current_user_id(request))
        .order_by(Chat.updated_at.desc())
    )
    return list(result.scalars())


@router.post("", response_model=ChatOut, status_code=201)
async def create_unfiled_chat(
    body: ChatCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Chat:
    """The counterpart to POST /projects/{id}/chats -- this one is how
    /chat's "New conversation" creates a chat with nothing to file it
    under. Moving it into a project later is PATCH /chats/{id}."""
    chat = Chat(project_id=None, user_id=_current_user_id(request), title=body.title)
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


@router.post("/{chat_id}/messages", response_model=TurnOut, status_code=201)
async def create_message(
    chat_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(session_dependency),
) -> TurnOut:
    """Send a message and get the answer to it.

    Synchronous on purpose: the reply is the response. Phase 5's memory write
    is what moves to the background, because that one takes 15-30s and the user
    should never wait on it.
    """
    if body.role != "user":
        raise HTTPException(
            status_code=422,
            detail="only user messages can be posted; the assistant turn is generated",
        )

    user_id = _current_user_id(request)
    chat = await _get_chat(session, chat_id, user_id)
    turn = await generate_turn(
        session,
        chat=chat,
        content=body.content,
        provider=_provider(request),
        user_id=user_id,
        memory=_memory(request),
        rag=_rag(request),
        title_model=request.app.state.settings.gate_model,
    )
    # Commit before scheduling, not after. FastAPI runs a background task
    # before the exit code of a dependency that yields, so session_dependency's
    # commit lands *after* the task -- the task would look up a message_id that
    # is not in the table yet and the provenance insert would fail on its
    # foreign key. Committing here makes the ordering explicit instead of
    # depending on framework internals.
    await session.commit()

    # Memory forms after the answer is sent, never in front of it: graphiti's
    # extraction takes 12-30s and nobody should wait on it to read their reply.
    memory = _memory(request)
    if memory is not None:
        background_tasks.add_task(
            run_write_gate,
            session_factory=request.app.state.session_factory,
            provider=_provider(request),
            memory=memory,
            gate_model=request.app.state.settings.gate_model,
            chat_id=chat.id,
            project_id=chat.project_id,
            user_id=user_id,
            message_id=turn.user_message.id,
            user_message=body.content,
            assistant_message=turn.assistant_message.content,
        )

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
            )
            for s in turn.rag_sources
        ],
    )


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
