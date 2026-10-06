"""ORM models mirroring db/migrations/.

The migrations are the source of truth for the schema; these classes only
describe it to SQLAlchemy. When you change one, change the other.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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


class Document(Base):
    """An uploaded file, and which tier it belongs to.

    ``project_id`` is optional for the same reason it is on ``Chat``: a
    document outside every project belongs to the user tier and is visible in
    every conversation, while a document inside one is isolated to it. The
    two tables agreeing on this shape is what lets the sidebar group files and
    chats the same way.

    Postgres holds this record; Qdrant holds the chunks, carrying ``id`` in
    their payload as ``document_id``. Deleting a row here means deleting by
    that filter there — there is no cascade between the two stores.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'ingested', 'failed')",
            name="documents_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    # Null until the document's topics reach the graph. Distinguishes "never
    # tried" from "tried and the gate kept nothing".
    graph_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
    # The episode text exactly as sent to the graph: every fact the gate kept
    # for this tier in one turn. Stored so a pending row can be retried after
    # a restart.
    body: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gate_decisions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GateDecisionRecord(Base):
    """One turn awaiting, or past, the write gate.

    The durable queue the write worker drains (write_queue.py) and the record
    of every decision, declines included -- the training data for replacing
    the gate's model call with a decision model.
    """

    __tablename__ = "gate_decisions"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'done', 'failed')",
            name="gate_decisions_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str] = mapped_column(Text, nullable=False)
    recalled_facts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    persist: Mapped[bool | None] = mapped_column(Boolean)
    facts: Mapped[list | None] = mapped_column(JSONB)
    # [{"id", "fact"}] retired from memory because this turn changed them.
    retired: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String)
    gate_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DecisionLogRecord(Base):
    """What the decision model said beside what the existing logic decided.
    See decisions.py and db/migrations/20261004130000_decision_log.sql."""

    __tablename__ = "decision_log"
    __table_args__ = (
        CheckConstraint("mode in ('shadow', 'live')", name="decision_log_mode_check"),
    )

    id: Mapped[uuid.UUID] = _pk()
    point: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    laya: Mapped[dict | None] = mapped_column(JSONB)
    baseline: Mapped[dict | None] = mapped_column(JSONB)
    laya_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


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
