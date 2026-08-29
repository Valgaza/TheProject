"""Chats and their messages.

Phase 1 stores and returns messages. Phase 2 adds generation to the POST
handler: retrieve, call the provider, store the assistant turn.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_dependency
from ..models import Chat, Message, Project
from ..schemas import ChatOut, MessageCreate, MessageOut

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
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    return list(result.scalars())


@router.post("/{chat_id}/messages", response_model=MessageOut, status_code=201)
async def create_message(
    chat_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Message:
    """Append a message. Generation arrives in phase 2."""
    await _get_chat(session, chat_id, _current_user_id(request))
    message = Message(chat_id=chat_id, role=body.role, content=body.content, meta={})
    session.add(message)
    await session.flush()
    return message
