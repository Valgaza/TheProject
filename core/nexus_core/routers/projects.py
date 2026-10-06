"""Projects, and the chats that live under them.

Real auth landed 2026-09-02 — user_id now comes from the session cookie
(see ../auth.py), not from settings. Exactly the "these handlers barely
change" the old docstring here predicted: every call site below is
unchanged, only where _current_user_id gets its answer moved.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id as _current_user_id
from ..db import session_dependency
from ..models import Chat, Project
from ..schemas import ChatCreate, ChatOut, ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> list[Project]:
    result = await session.execute(
        select(Project)
        .where(Project.user_id == _current_user_id(request))
        .order_by(Project.updated_at.desc())
    )
    return list(result.scalars())


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Project:
    project = Project(
        user_id=_current_user_id(request),
        name=body.name,
        description=body.description,
        domain=body.domain,
    )
    session.add(project)
    await session.flush()
    return project


async def _get_project(session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Project:
    return await _get_project(session, project_id, _current_user_id(request))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Project:
    """Rename or pin/unpin -- whichever fields are set. No move, the way
    chats have one: a project isn't inside anything to move between."""
    if body.name is None and body.description is None and body.pinned is None:
        raise HTTPException(status_code=422, detail="nothing to update")

    project = await _get_project(session, project_id, _current_user_id(request))
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.pinned is not None:
        project.pinned = body.pinned

    await session.flush()
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> None:
    """Removes the project and every chat inside it -- a much bigger blast
    radius than deleting a single chat, so the frontend needs a confirm
    step that actually says so. Same rule as chat deletion: anything the
    write gate already extracted into the knowledge graph survives: Neo4j
    isn't touched by this at all, and its group_ids are already namespaced
    by project_id, not implicitly renumbered if this project_id is reused
    (it never will be -- ids aren't reused)."""
    project = await _get_project(session, project_id, _current_user_id(request))
    await session.delete(project)


@router.get("/{project_id}/chats", response_model=list[ChatOut])
async def list_chats(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> list[Chat]:
    await _get_project(session, project_id, _current_user_id(request))
    result = await session.execute(
        select(Chat).where(Chat.project_id == project_id).order_by(Chat.updated_at.desc())
    )
    return list(result.scalars())


@router.post("/{project_id}/chats", response_model=ChatOut, status_code=201)
async def create_chat(
    project_id: uuid.UUID,
    body: ChatCreate,
    request: Request,
    session: AsyncSession = Depends(session_dependency, scope="function"),
) -> Chat:
    project = await _get_project(session, project_id, _current_user_id(request))
    chat = Chat(user_id=project.user_id, project_id=project_id, title=body.title)
    session.add(chat)
    await session.flush()
    return chat
