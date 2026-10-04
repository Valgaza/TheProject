"""
RAG API — HTTP interface to the RAG pipeline.

Called by core (core/nexus_core/rag_client.py) over the network — core and
this service are separate processes, so this is the only way core can reach
rag_pipeline. Not a user-facing surface; nobody outside core is meant to call
this directly. See .github/CONTROLLER_SERVICE.md for the fuller "why is this
a separate service" writeup.

Exposes:
  POST /api/ingest   — upload a file (PDF/MD), ingest into Qdrant
  POST /api/query    — send a question, get back the assembled LLM prompt + sources
  GET  /api/status   — cheap collection stats, no embedding/search/rerank
  GET  /api/health   — liveness check
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

# rag_pipeline is a real installed package now (RAG/ is a uv workspace
# member) — no sys.path manipulation needed to import it, unlike when this
# file lived in a separate controller/ folder outside the RAG package.
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=True)
# The repo-root .env too, without overriding: SERVICE_TOKEN lives there,
# shared with core and memory, and pydantic's env_file never reaches os.environ.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rag_pipeline.scopes import read_scopes, write_scope
from rag_pipeline.service_auth import ServiceTokenMiddleware
from rag_pipeline.pipelines.ingestion import IngestionPipeline
from rag_pipeline.pipelines.query import QueryPipeline

# ── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("rag-api")

# ── FastAPI app ───────────────────────────────────────────────────────────
app = FastAPI(
    title="RAG API",
    description="HTTP interface to the RAG pipeline, called by core.",
    version="0.1.0",
)

# No CORS: core is the only caller, and it is a server. The browser's uploads
# go through core's /documents, which knows who is uploading and to where.
app.add_middleware(ServiceTokenMiddleware)
if not os.environ.get("SERVICE_TOKEN"):
    logger.error("SERVICE_TOKEN is not set: every request except /api/health will be refused")

# ── Lazy-initialized singletons (avoid cold-start on import) ─────────────
_ingest_pipeline: IngestionPipeline | None = None
_query_pipeline: QueryPipeline | None = None


def _get_ingest_pipeline() -> IngestionPipeline:
    global _ingest_pipeline
    if _ingest_pipeline is None:
        logger.info("Initializing ingestion pipeline...")
        _ingest_pipeline = IngestionPipeline()
    return _ingest_pipeline


def _get_query_pipeline() -> QueryPipeline:
    global _query_pipeline
    if _query_pipeline is None:
        logger.info("Initializing query pipeline...")
        _query_pipeline = QueryPipeline()
    return _query_pipeline


# ── Response models ──────────────────────────────────────────────────────

class IngestResponse(BaseModel):
    success: bool
    file_name: str
    documents_processed: int
    chunks_created: int
    embeddings_generated: int
    points_upserted: int
    elapsed_seconds: float
    errors: list[str]


class SourceInfo(BaseModel):
    file_name: str
    page_number: int | str | None = None
    chunk_index: int | str | None = None
    heading_path: str = ""
    rerank_score: float = 0.0


class QueryResponse(BaseModel):
    query: str
    assembled_prompt: str
    sources: list[SourceInfo]
    total_chunks_used: int
    total_chars: int
    retrieval_count: int
    reranked_count: int
    elapsed_seconds: float


# ── Endpoints ────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown", ".mdx"}


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    project_id: str | None = Form(None),
    document_id: str | None = Form(None),
):
    """Upload and ingest a document into one tier of the vector store.

    ``user_id`` is required rather than optional. An unowned chunk is
    invisible to every scoped read, so accepting an upload without an owner
    would succeed loudly and then retrieve nothing forever -- the worst shape
    a bug can take. No project means the universal tier, exactly as a chat
    with no project writes to the user tier.

    ``document_id`` is the Postgres row this file belongs to. It is what
    makes deletion possible: Postgres owns the record, and this is the only
    key linking it to the chunks out here.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Write to a temp file so the pipeline can read it
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="rag_upload_") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Rename to preserve original filename (loaders use it for metadata)
        dest = tmp_path.parent / file.filename
        tmp_path.rename(dest)

        scope_key = write_scope(user_id, project_id)
        logger.info(
            "Ingesting %s (%d bytes) into %s",
            file.filename, len(content), scope_key,
        )
        pipeline = _get_ingest_pipeline()
        stats = pipeline.ingest(dest, extra_payload={
            "user_id": user_id,
            "project_id": project_id,
            "document_id": document_id,
            "scope_key": scope_key,
        })

        return IngestResponse(
            success=len(stats.errors) == 0,
            file_name=file.filename,
            documents_processed=stats.documents_processed,
            chunks_created=stats.chunks_created,
            embeddings_generated=stats.embeddings_generated,
            points_upserted=stats.points_upserted,
            elapsed_seconds=round(stats.elapsed_seconds, 2),
            errors=stats.errors,
        )
    finally:
        # Clean up temp file
        for p in [dest, tmp_path]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


@app.post("/api/query", response_model=QueryResponse)
async def query_knowledge_base(
    question: str = Form(...),
    user_id: str = Form(...),
    project_id: str | None = Form(None),
):
    """Query one person's documents, in the tiers this question may read.

    A question inside a project retrieves from that project *and* from the
    universal tier; a question outside every project retrieves from the
    universal tier alone. That asymmetry lives in ``read_scopes`` and mirrors
    the graph's ``read_groups`` -- see the note there before changing either.
    """
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    scopes = read_scopes(user_id, project_id)
    logger.info("Query %r across %s", question[:80], scopes)
    pipeline = _get_query_pipeline()
    # A list value becomes Qdrant's MatchAny -- an OR across the tiers -- so
    # this one filter is the whole access rule.
    result = pipeline.query(
        question=question.strip(),
        filters={"scope_key": scopes},
    )

    ctx = result.assembled_context

    sources = []
    for src in ctx.sources:
        sources.append(SourceInfo(
            file_name=src.get("file_name", "—"),
            page_number=src.get("page_number"),
            chunk_index=src.get("chunk_index"),
            heading_path=src.get("heading_path", ""),
            rerank_score=round(src.get("rerank_score", 0), 4),
        ))

    return QueryResponse(
        query=result.query,
        assembled_prompt=ctx.prompt,
        sources=sources,
        total_chunks_used=ctx.total_chunks_used,
        total_chars=ctx.total_chars,
        retrieval_count=len(result.retrieval_results),
        reranked_count=len(result.reranked_results),
        elapsed_seconds=round(result.elapsed_seconds, 2),
    )


@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "service": "rag-api"}


@app.get("/api/status")
async def status(user_id: str | None = None, project_id: str | None = None):
    """Cheap collection stats -- no embedding, search, or reranking.

    With ``user_id`` this counts only the tiers that user's question could
    read, which is what core needs: "are there documents *here*", not "are
    there documents anywhere". Without it, the whole-collection view, kept
    for operators poking at the service directly.
    """
    pipeline = _get_query_pipeline()
    info = pipeline.collection_info()
    if user_id:
        scopes = read_scopes(user_id, project_id)
        info = dict(info)
        info["scope_keys"] = scopes
        info["points_count"] = pipeline.count_in_scopes(scopes)
    return info


@app.get("/api/documents/{document_id}/excerpt")
async def document_excerpt(document_id: str, chars: int = 4000):
    """The opening of a document, as text.

    For callers that need to know what a file is about -- cataloguing it into
    a knowledge graph, say -- without re-parsing it. The chunks here have
    already been through the loader and chunker, so this is the cleaned text,
    not the raw bytes.
    """
    pipeline = _get_query_pipeline()
    chunks = pipeline.opening_chunks(document_id, limit=6)
    text = "\n\n".join(c for c in chunks if c)
    return {"document_id": document_id, "excerpt": text[:chars]}


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    """Remove one document's chunks.

    Postgres owns the record and deletes its own row; this removes what that
    row pointed at. Nothing cascades between the two stores, so a caller that
    deletes the row without calling this leaves orphaned chunks that will
    keep answering questions.
    """
    pipeline = _get_query_pipeline()
    removed = pipeline.delete_document(document_id)
    return {"document_id": document_id, "chunks_removed": removed}
