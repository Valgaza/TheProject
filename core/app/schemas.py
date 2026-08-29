"""Request and response bodies for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant", "system"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    domain: str = "general"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    domain: str
    created_at: datetime
    updated_at: datetime


class ChatCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    role: Role = "user"


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_id: uuid.UUID
    role: Role
    content: str
    meta: dict = Field(serialization_alias="metadata")
    created_at: datetime


class RouteOut(BaseModel):
    """Which handler answered, and why it was picked."""

    handler: str
    reason: str


class TurnOut(BaseModel):
    """Both halves of an exchange.

    The user message comes back rather than only the answer so the client can
    reconcile its optimistic render against the stored row — the id and the
    server timestamp are the server's to assign.
    """

    user_message: MessageOut
    assistant_message: MessageOut
    route: RouteOut
    chat_title: str
