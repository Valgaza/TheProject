import httpx

from ..config import settings


async def has_documents() -> bool:
    """Cheap check: is there anything at all in the RAG collection, so it's
    worth even bothering to call query_documents()?

    IMPORTANT CAVEAT: RAG/rag_pipeline currently has no per-user or
    per-tenant scoping — controller/main.py's /api/ingest and /api/query
    don't take a user_id at all, and everything lives in one shared Qdrant
    collection. So this checks "does ANYONE'S documents exist," not "does
    THIS user's documents exist." That's a real gap worth a decision on its
    own (do multiple users of one deployment need document isolation from
    each other?) — this function is not pretending to solve that.
    """
    async with httpx.AsyncClient(timeout=settings.rag_timeout) as client:
        response = await client.get(f"{settings.rag_url}/api/status")
        response.raise_for_status()
        info = response.json()
    return info.get("points_count", 0) > 0


async def query_documents(question: str) -> dict:
    """Ask the RAG controller (controller/main.py, POST /api/query) to
    retrieve + rerank relevant document chunks and assemble them into a
    prompt. Returns the raw JSON response — see controller/main.py's
    QueryResponse for the exact shape (assembled_prompt, sources, etc).
    """
    # httpx.AsyncClient is an HTTP client that works with async/await, so
    # this call doesn't block the orchestrator's event loop while it waits
    # for the RAG controller to respond — other requests can still be
    # handled in the meantime. "async with" makes sure the connection gets
    # closed automatically once we're done, even if an error happens.
    async with httpx.AsyncClient(timeout=settings.rag_timeout) as client:
        response = await client.post(
            f"{settings.rag_url}/api/query",
            # The controller's endpoint expects a form field named
            # "question" (see controller/main.py: `question: str =
            # Form(...)`), not JSON — so we send it as form data, not json=.
            data={"question": question},
        )
        # Raises an exception if the controller responded with an error
        # status code (4xx/5xx), instead of silently returning bad data.
        response.raise_for_status()
        return response.json()
