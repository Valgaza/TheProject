"""Chats and their messages.

Posting a message runs a full turn: route it, store it, answer it, store the
answer. Retrieval joins that sequence in phase 4.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat_service import generate_turn
from ..db import session_dependency
from ..models import Chat, Message, Project
from ..providers.base import Provider
from ..schemas import ChatOut, MessageCreate, MessageOut, RouteOut, TurnOut

router = APIRouter(prefix="/chats", tags=["chats"])


async def _get_chat(session: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID) -> Chat:
    """Load a chat, refusing one that belongs to somebody else's project."""
    result = await session.execute(
        select(Chat).join(Project).where(Chat.id == chat_id, Project.user_id == user_id)
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _current_user_id(request: Request) -> uuid.UUID:
    return request.app.state.settings.demo_user_id


def _provider(request: Request) -> Provider:
    return request.app.state.provider


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

    chat = await _get_chat(session, chat_id, _current_user_id(request))
    turn = await generate_turn(
        session,
        chat=chat,
        content=body.content,
        provider=_provider(request),
    )
    return TurnOut(
        user_message=MessageOut.model_validate(turn.user_message),
        assistant_message=MessageOut.model_validate(turn.assistant_message),
        route=RouteOut(handler=turn.route.handler, reason=turn.route.reason),
        chat_title=chat.title,
    )
