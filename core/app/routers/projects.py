"""Projects, and the chats that live under them.

Auth is deferred, so every project belongs to the single configured demo user.
When Supabase Auth lands, ``user_id`` comes from the verified token instead of
settings and these handlers barely change.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_dependency
from ..models import Chat, Project
from ..schemas import ChatCreate, ChatOut, ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _current_user_id(request: Request) -> uuid.UUID:
    return request.app.state.settings.demo_user_id


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
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
    session: AsyncSession = Depends(session_dependency),
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
    session: AsyncSession = Depends(session_dependency),
) -> Project:
    return await _get_project(session, project_id, _current_user_id(request))


@router.get("/{project_id}/chats", response_model=list[ChatOut])
async def list_chats(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
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
    session: AsyncSession = Depends(session_dependency),
) -> Chat:
    await _get_project(session, project_id, _current_user_id(request))
    chat = Chat(project_id=project_id, title=body.title)
    session.add(chat)
    await session.flush()
    return chat
