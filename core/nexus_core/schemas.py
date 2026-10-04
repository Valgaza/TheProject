"""Request and response bodies for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["user", "assistant", "system"]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProfileOut(BaseModel):
    """A logged-in user. Never includes password_hash — this is what the
    frontend gets back, not what's in the database row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    email: str


class AuthConfigOut(BaseModel):
    """What the login page needs to know before anyone has logged in yet --
    whether to render the Google button at all. Read with no session, so it
    carries nothing about who's asking, only what this server supports."""

    google_oauth_enabled: bool


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
    pinned: bool
    created_at: datetime
    updated_at: datetime


class ProjectUpdate(BaseModel):
    """Rename and pin/unpin, as one partial update -- each field left unset
    leaves that column alone. No move-to-project equivalent here; a project
    isn't inside anything to move between."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    pinned: bool | None = None


class ChatCreate(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)
    # Only meaningful on POST /chats. Null starts a chat outside every project,
    # which reads and writes the user memory tier alone.
    project_id: uuid.UUID | None = None


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # None for a chat created from /chat directly, not filed under a project.
    project_id: uuid.UUID | None
    title: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class ChatUpdate(BaseModel):
    """Rename, pin/unpin, and move-to-project, all as one partial update --
    each field left unset leaves that column alone. Nothing required, but
    an empty body is rejected by the router rather than silently no-op'd."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None
    project_id: uuid.UUID | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    role: Role = "user"
    # The composer's web button. Only ever set by the person: web search is
    # the one handler that sends text off the machine.
    use_web: bool = False


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


class RagSourceOut(BaseModel):
    """A document chunk retrieved from RAG and shown to the model for this
    turn — same idea as FactOut, one level of provenance the UI can render."""

    file_name: str
    page_number: int | str | None = None
    chunk_index: int | str | None = None
    heading_path: str = ""
    # How strongly the reranker matched this chunk to the question, 0-1.
    # Surfaced because "why did it cite this?" is the first question anyone
    # asks about a wrong answer, and the score is the honest answer.
    rerank_score: float = 0.0


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
    # What RAG contributed. Empty when the route wasn't "rag", nothing was
    # retrieved, or RAG was unreachable -- same non-fatal contract as memory.
    rag_sources: list[RagSourceOut] = []


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


class DocumentOut(BaseModel):
    """An uploaded file as the UI sees it.

    ``project_id`` being null is meaningful rather than missing: it is the
    universal tier, readable from every chat. The same null on ``ChatOut``
    means the same thing, which is why they render in the same tree.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    file_name: str
    byte_size: int
    chunk_count: int
    status: str
    error: str | None
    graph_synced_at: datetime | None
    created_at: datetime
