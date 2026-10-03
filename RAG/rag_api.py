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
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# rag_pipeline is a real installed package now (RAG/ is a uv workspace
# member) — no sys.path manipulation needed to import it, unlike when this
# file lived in a separate controller/ folder outside the RAG package.
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=True)

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

app.add_middleware(
    CORSMiddleware,
    # Only relevant if a browser ever calls this directly, which nothing is
    # meant to do going forward — core (a server, not a browser) is the
    # intended caller, and CORS doesn't apply to server-to-server calls at
    # all. Left permissive for now since the frontend's old demo pages still
    # exist; revisit once those are retired (see steps-to-implement doc).
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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


def _scope_filter(user_id: str, project_id: str | None) -> dict[str, str]:
    """The filter a scoped read should apply.

    A project already belongs to exactly one user (enforced in core's own
    Postgres schema, not re-checked here), so matching project_id alone is
    enough once one is given. No project means "this user's own, unfiled
    uploads" -- user_id plus the "" project_id sentinel, so another user's
    loose uploads (or this user's project-filed ones) don't leak in.
    """
    if project_id:
        return {"project_id": project_id}
    return {"user_id": user_id, "project_id": ""}


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    project_id: str | None = Form(None),
):
    """Upload and ingest a document into the vector store, tagged with who
    it belongs to. Every real caller is core (rag_client.py), which always
    has an authenticated user_id in hand; there's no legitimate unscoped
    upload through this HTTP surface, unlike IngestionPipeline.ingest()
    itself, which stays optional for direct/CLI use.
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

        logger.info(
            "Ingesting uploaded file: %s (%d bytes) user_id=%s project_id=%s",
            file.filename, len(content), user_id, project_id,
        )
        pipeline = _get_ingest_pipeline()
        stats = pipeline.ingest(dest, user_id=user_id, project_id=project_id or "")

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
    """Query the knowledge base and return the assembled prompt, scoped to
    whichever documents this user (or their current project) actually owns.
    """
    if not question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    logger.info("Query: %s (user_id=%s project_id=%s)", question[:120], user_id, project_id)
    pipeline = _get_query_pipeline()
    result = pipeline.query(
        question=question.strip(),
        filters=_scope_filter(user_id, project_id),
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
async def status(
    user_id: str = Query(...),
    project_id: str | None = Query(None),
):
    """Cheap, scoped existence check — no embedding, search, or reranking
    involved. Used by core to decide whether THIS user's (or project's) RAG
    is worth querying at all before bothering to run a full query. Scoped
    the same way /api/query is, so has_documents() and query() always agree
    on what "this project/user has documents" means.
    """
    pipeline = _get_query_pipeline()
    points_count = pipeline.scoped_point_count(_scope_filter(user_id, project_id))
    return {"points_count": points_count}
