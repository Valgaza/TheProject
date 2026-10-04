"""Uploaded documents, and which tier they belong to.

Two stores, one operation. Postgres holds the record a person sees in a file
list; Qdrant holds the chunks that answer their questions. Nothing cascades
between them, so every handler here is responsible for keeping the two in
step -- and for leaving the record behind when the chunks fail, rather than
pretending the upload never happened.

Scope works exactly as it does for chats. No project means the universal
tier, readable from every conversation. A project means isolated to it. The
rule lives in ``rag_pipeline.scopes.read_scopes`` and mirrors the graph's
``read_groups``; this module only decides which ids to hand over.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user_id as _current_user_id
from ..db import session_dependency
from ..document_gate import sync_document_to_graph
from ..memory_client import MemoryClient
from ..models import Document, Project
from ..rag_client import RagClient
from ..schemas import DocumentOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# Matches RAG's own ALLOWED_EXTENSIONS. Checked here as well so a wrong file
# is refused before it is read into memory and posted across a service
# boundary, rather than after.
ALLOWED_SUFFIXES = {".pdf", ".md", ".markdown", ".mdx"}

# Big enough for a textbook chapter, small enough that a mis-drag of a video
# file fails fast instead of occupying the process for a minute.
MAX_BYTES = 32 * 1024 * 1024


def _rag(request: Request) -> RagClient | None:
    return getattr(request.app.state, "rag", None)


def _memory(request: Request) -> MemoryClient | None:
    return getattr(request.app.state, "memory", None)


async def _owned_project(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.user_id != user_id:
        # 404 rather than 403: whether a project exists is not something a
        # non-owner should be able to learn from a status code.
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    request: Request,
    project_id: uuid.UUID | None = None,
    loose: bool = False,
    session: AsyncSession = Depends(session_dependency),
) -> list[Document]:
    """This user's documents, newest first.

    Three views, matching how the sidebar asks about chats: everything by
    default, one project's files with ``project_id``, and the universal tier
    alone with ``loose=true``.
    """
    query = select(Document).where(Document.user_id == _current_user_id(request))
    if project_id is not None:
        query = query.where(Document.project_id == project_id)
    elif loose:
        query = query.where(Document.project_id.is_(None))
    result = await session.execute(query.order_by(Document.created_at.desc()))
    return list(result.scalars())


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(session_dependency),
) -> Document:
    """Take a file, record it, chunk it, and tell the graph about it.

    The record is written *before* the ingest and left behind if the ingest
    fails, with the failure on it. A file that vanished because its chunking
    threw is a file the person cannot retry or even find; one sitting in the
    list marked failed is a problem they can act on.
    """
    user_id = _current_user_id(request)
    if project_id is not None:
        await _owned_project(session, project_id, user_id)

    if not file.filename:
        raise HTTPException(status_code=400, detail="the file has no name")
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"{suffix or 'that file type'} can't be read. "
                   f"Supported: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="the file is empty")
    if len(content) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"the file is {len(content) // 1_048_576} MB; the limit is "
                   f"{MAX_BYTES // 1_048_576} MB",
        )

    document = Document(
        user_id=user_id,
        project_id=project_id,
        file_name=file.filename,
        byte_size=len(content),
        status="pending",
    )
    session.add(document)
    await session.flush()

    rag = _rag(request)
    if rag is None:
        document.status = "failed"
        document.error = "the document service isn't configured"
        return document

    try:
        stats = await rag.ingest(
            file_name=file.filename,
            content=content,
            content_type=file.content_type,
            user_id=user_id,
            project_id=project_id,
            document_id=document.id,
        )
    except Exception as exc:
        logger.exception("Ingest failed for %s", document.id)
        document.status = "failed"
        document.error = f"{exc.__class__.__name__}: {exc}"
        return document

    document.chunk_count = int(stats.get("points_upserted", 0))
    errors = stats.get("errors") or []
    if errors or not document.chunk_count:
        document.status = "failed"
        document.error = "; ".join(errors) or "nothing could be read from this file"
        return document

    document.status = "ingested"

    # Commit before scheduling, not after -- the same trap chats.py documents
    # for the write gate. FastAPI runs a background task *before* the exit
    # code of a dependency that yields, so session_dependency's commit lands
    # after the task. The task opens its own session, finds no row for a
    # document that has not been committed yet, and does nothing at all.
    await session.commit()

    # Teaching the graph runs after the response, like the write gate does
    # after a turn. It costs a model call, and nobody uploading a file should
    # wait on the graph to finish thinking about it.
    memory = _memory(request)
    if memory is not None:
        background_tasks.add_task(
            sync_document_to_graph,
            session_factory=request.app.state.session_factory,
            memory=memory,
            rag=rag,
            provider=request.app.state.provider,
            document_id=document.id,
            gate_model=request.app.state.settings.gate_model,
        )

    return document


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> None:
    """Remove a document from both stores.

    Chunks first, then the row. That order matters: if the chunk delete fails
    the row survives and the file is still listed, so the person can try
    again. The other order would leave chunks nothing points at, still
    answering questions about a file that no longer appears anywhere.
    """
    user_id = _current_user_id(request)
    document = await session.get(Document, document_id)
    if document is None or document.user_id != user_id:
        raise HTTPException(status_code=404, detail="document not found")

    rag = _rag(request)
    if rag is not None:
        removed = await rag.delete_document(document_id)
        logger.info("Removed %d chunks for document %s", removed, document_id)

    await session.delete(document)

    # The graph keeps what it learned. Graphiti's whole model is mark rather
    # than delete -- superseded edges expire instead of vanishing, which is
    # what makes contradiction repair auditable -- and deleting a PDF is not
    # evidence the person stopped knowing about its subject. If that turns
    # out to be wrong, hygiene.py is where the policy belongs, not here.
