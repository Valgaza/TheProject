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


class FactOut(BaseModel):
    """A fact recalled from memory and shown to the model for this turn."""

    id: str
    fact: str
    scope: str
    kind: str = "relationship"


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
    # What memory contributed. Empty when nothing was recalled or the memory
    # service was unreachable -- the answer still stands either way.
    recalled: list[FactOut] = []


class MemoryEpisodeOut(BaseModel):
    """One attempt by the write gate to remember something from a chat.

    Exposed because the gate runs in the background: without this there is no
    way for a client -- or a test -- to know whether a turn left anything behind,
    or why it did not.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chat_id: uuid.UUID
    message_id: uuid.UUID | None
    episode_uuid: uuid.UUID | None
    scope: str
    status: str
    reason: str | None
    error: str | None
    created_at: datetime
