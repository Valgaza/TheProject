"""ORM models mirroring supabase/migrations/.

The migrations are the source of truth for the schema; these classes only
describe it to SQLAlchemy. When you change one, change the other.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = _pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Login identity, added 2026-09-02 (see the auth migration). password_hash
    # is null for a Google-only account, google_id is null for a
    # password-only one — the database itself requires at least one, via a
    # CHECK constraint, not application code.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    google_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text, nullable=False, default="general")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # passive_deletes=True: same reasoning as Chat.messages below -- trust
    # the FK's own ON DELETE CASCADE instead of the ORM loading every chat
    # first and trying to null out project_id, which fails outright since
    # that column is NOT NULL.
    chats: Mapped[list["Chat"]] = relationship(back_populates="project", passive_deletes=True)


class Chat(Base):
    """One conversation.

    ``project_id`` is optional: a chat outside every project reads and writes
    the user tier alone. Ownership lives on the row rather than being derived
    through the project, because there may not be a project to derive it from.
    """

    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = _pk()
    # Who owns this chat -- independent of project_id, since a chat no
    # longer has to be filed under a project to exist. Ownership used to
    # come entirely through project_id -> projects.user_id; that join
    # breaks the moment project_id can be null, hence this column.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Null means unfiled: created from /chat directly, not from within a
    # project. Set at creation (POST /projects/{id}/chats) or later via
    # PATCH /chats/{id}'s move-to-project.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="New conversation")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped[Project | None] = relationship(back_populates="chats")
    # passive_deletes=True: trust the FK's own ON DELETE CASCADE (see the
    # schema migration) instead of the ORM's default behaviour, which loads
    # every message first and tries to null out chat_id -- a write that
    # fails outright, since chat_id is NOT NULL.
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", passive_deletes=True
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'system')", name="messages_role_check"),
    )

    id: Mapped[uuid.UUID] = _pk()
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Which handler answered, retrieved fact ids, token counts, latency.
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    # clock_timestamp(), not now(): a turn writes the question and the answer in
    # one transaction, and now() would give them the same timestamp.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )

    chat: Mapped[Chat] = relationship(back_populates="messages")


class MemoryEpisode(Base):
    """Links a graphiti episode back to the chat that produced it.

    Exists because graphiti 0.29 declares ``EpisodicNode.episode_metadata`` but
    never persists it, so the episode itself cannot carry its provenance.
    """

    __tablename__ = "memory_episodes"
    __table_args__ = (
        CheckConstraint("scope in ('user', 'project')", name="memory_episodes_scope_check"),
        CheckConstraint(
            "status in ('pending', 'ingested', 'failed')",
            name="memory_episodes_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    episode_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # Why the gate decided what it did; the field you read when tuning it.
    reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MaintenanceRun(Base):
    """One pass of the graph tidy-up, and what it decided.

    Recorded because the sweep marks rather than deletes: its decisions are
    reversible, but only if you can find them afterwards.
    """

    __tablename__ = "maintenance_runs"
    __table_args__ = (
        CheckConstraint("scope in ('user', 'project')", name="maintenance_runs_scope_check"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(String, nullable=False)
    # The graphiti group this run swept. Text, not a foreign key: it is the
    # memory service's identifier, not a row in this database.
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Episodes in this group when the run fired. The automatic trigger compares
    # against this rather than a clock: what matters is how much the graph
    # changed, not how long it has been.
    episodes_at_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
