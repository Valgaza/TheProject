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
    # Only meaningful on POST /chats. Null starts a chat outside every project,
    # which reads and writes the user memory tier alone.
    project_id: uuid.UUID | None = None


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
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


class MaintenanceRequest(BaseModel):
    """One tidy-up of whatever a view covers."""

    view: Literal["all", "user", "project"] = "all"
    project_id: uuid.UUID | None = None
    # Reports without changing anything unless the caller asks. The sweep will
    # be wrong sometimes and the diff is worth reading first.
    dry_run: bool = True


class MaintenanceRunOut(BaseModel):
    """One recorded pass. The report is the sweep's own, passed through."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    scope: str
    group_id: str
    dry_run: bool
    report: dict
    episodes_at_run: int
    started_at: datetime
    finished_at: datetime | None
