"""Tidying the graph, and keeping a record of it.

The sweep itself lives in the memory service, which is the only thing that
should touch Neo4j. This is here because the record belongs in Postgres, and
because giving the memory service a database connection to hold a counter would
blur a split that is otherwise clean: core owns what was said, memory owns what
was learned.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..auth import current_user_id as _current_user_id
from ..db import session_dependency
from ..memory_client import MemoryClient
from ..models import Chat, MaintenanceRun, MemoryEpisode, Project
from ..schemas import MaintenanceRequest, MaintenanceRunOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

# How many new episodes a group takes before it is swept without being asked.
#
# A counter rather than a timer. The graph only changes when you talk to it, so
# a scheduled sweep would do idle work on an app that is off most of the time,
# and would need a scheduler to do it.
EPISODES_BETWEEN_SWEEPS = 10


async def _record(
    session_factory: async_sessionmaker,
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    view: str,
    dry_run: bool,
    memory: MemoryClient,
    episodes_at_run: int,
) -> dict:
    """Run the sweep and write one row per group it touched."""
    started = datetime.now(timezone.utc)
    body = await memory.maintenance(
        user_id=user_id, project_id=project_id, view=view, dry_run=dry_run
    )
    finished = datetime.now(timezone.utc)

    async with session_factory() as session:
        for report in body.get("reports", []):
            session.add(
                MaintenanceRun(
                    user_id=user_id,
                    # A user-tier sweep has no project even when it was
                    # launched from inside one.
                    project_id=project_id if report["scope"] == "project" else None,
                    scope=report["scope"],
                    group_id=report["group_id"],
                    dry_run=dry_run,
                    report=report,
                    episodes_at_run=episodes_at_run,
                    started_at=started,
                    finished_at=finished,
                )
            )
        await session.commit()
    return body


async def maybe_sweep(
    *,
    session_factory: async_sessionmaker,
    memory: MemoryClient,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    scope: str,
) -> None:
    """Sweep this group if it has taken enough new episodes since the last one.

    Runs after the write gate, in the background, and swallows everything: a
    maintenance pass failing must never surface as a failed conversation turn.
    """
    try:
        async with session_factory() as session:
            # Episodes in this scope, all-time, against the count recorded on
            # the last real run. A dry run changes nothing, so it does not
            # reset the counter.
            # Matched on the tier, not on the graphiti group id: the group id
            # is the memory service's to mint and only arrives back on the
            # report, so it is not a key this side can look up by.
            previous = select(func.max(MaintenanceRun.episodes_at_run)).where(
                MaintenanceRun.user_id == user_id,
                MaintenanceRun.scope == scope,
                MaintenanceRun.dry_run.is_(False),
            )
            previous = (
                previous.where(MaintenanceRun.project_id == project_id)
                if scope == "project"
                else previous.where(MaintenanceRun.project_id.is_(None))
            )
            last = await session.scalar(previous)
            written = await _episodes_in_scope(session, user_id, project_id, scope)

        if written - (last or 0) < EPISODES_BETWEEN_SWEEPS:
            return

        logger.info(
            "auto-sweep scope=%s project=%s episodes=%d since_last=%d",
            scope,
            project_id,
            written,
            written - (last or 0),
        )
        await _record(
            session_factory,
            user_id=user_id,
            project_id=project_id,
            view=scope,
            dry_run=False,
            memory=memory,
            episodes_at_run=written,
        )
    except Exception:
        logger.exception("Automatic sweep failed for scope=%s project=%s", scope, project_id)


async def _episodes_in_scope(
    session: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None,
    scope: str,
) -> int:
    """How many episodes have ever been ingested into this tier."""
    query = (
        select(func.count(MemoryEpisode.id))
        .join(Chat, Chat.id == MemoryEpisode.chat_id)
        .where(
            Chat.user_id == user_id,
            MemoryEpisode.status == "ingested",
            MemoryEpisode.scope == scope,
        )
    )
    if scope == "project":
        query = query.where(Chat.project_id == project_id)
    return await session.scalar(query) or 0


@router.post("")
async def run_maintenance(
    body: MaintenanceRequest,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> dict:
    """Tidy the graph behind one view, on demand.

    Defaults to a dry run. This pass will be wrong sometimes, and the diff is
    worth reading before it is applied.
    """
    user_id = _current_user_id(request)
    memory: MemoryClient | None = getattr(request.app.state, "memory", None)
    if memory is None:
        raise HTTPException(status_code=503, detail="memory service is not configured")

    if body.view == "project" and body.project_id is None:
        raise HTTPException(status_code=422, detail="the project view requires a project_id")

    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None or project.user_id != user_id:
            raise HTTPException(status_code=404, detail="project not found")

    scope = "project" if body.view == "project" else "user"
    written = await _episodes_in_scope(session, user_id, body.project_id, scope)

    try:
        return await _record(
            request.app.state.session_factory,
            user_id=user_id,
            project_id=body.project_id,
            view=body.view,
            dry_run=body.dry_run,
            memory=memory,
            episodes_at_run=written,
        )
    except Exception as exc:
        logger.exception("Maintenance failed")
        raise HTTPException(status_code=502, detail=f"memory service: {exc}") from exc


@router.get("/runs", response_model=list[MaintenanceRunOut])
async def list_runs(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
    limit: int = 20,
) -> list[MaintenanceRun]:
    """The last few passes, newest first."""
    result = await session.execute(
        select(MaintenanceRun)
        .where(MaintenanceRun.user_id == _current_user_id(request))
        .order_by(MaintenanceRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars())
