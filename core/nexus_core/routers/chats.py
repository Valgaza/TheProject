"""Chats and their messages.

Posting a message runs a full turn: route it, store it, answer it, store the
answer. Retrieval joins that sequence in phase 4.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_service import generate_turn
from ..db import session_dependency
from ..models import Chat, MemoryEpisode, Message, Project
from ..memory_client import MemoryClient
from ..memory_writer import run_write_gate
from ..providers.base import Provider
from ..schemas import (
    ChatCreate,
    ChatOut,
    FactOut,
    MemoryEpisodeOut,
    MessageCreate,
    MessageOut,
    RouteOut,
    TurnOut,
)

router = APIRouter(prefix="/chats", tags=["chats"])


async def _get_chat(session: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID) -> Chat:
    """Load a chat, refusing one that belongs to somebody else.

    Ownership is read off the chat rather than joined through the project: a
    chat outside every project has no project to join to.
    """
    result = await session.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _current_user_id(request: Request) -> uuid.UUID:
    return request.app.state.settings.demo_user_id


def _provider(request: Request) -> Provider:
    return request.app.state.provider


def _memory(request: Request) -> MemoryClient | None:
    return getattr(request.app.state, "memory", None)


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
            # What memory already supplied for this turn. The gate refuses to
            # restate any of it, which is what stops recall from making a fact
            # louder every time it is recalled.
            recalled_facts=[f.fact for f in turn.facts],
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
