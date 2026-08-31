import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .clients import graph_client, rag_client, web_client
from .clients.llm_client import llm_client
from .config import settings
from .retry import with_retry
from .router import decide

# Standard Python logging setup — logger.warning(...) below writes to the
# server's console/log file, separate from anything sent back in the HTTP
# response. This is what makes a failure visible to us (the developers)
# even when we choose not to expose the details to the caller.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

app = FastAPI(title="Nexus Orchestrator")

app.add_middleware(
    CORSMiddleware,
    # Only these origins (frontend URLs) are allowed to call this API from
    # a browser — NOT "*" (everyone). With "*", any website open in a
    # user's browser tab could make authenticated-looking requests to this
    # API using the user's own local network access, which is a real risk
    # once this handles medical/legal data. settings.cors_origins comes
    # from the CORS_ORIGINS env var, defaulting to just the local frontend.
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    user_id: str
    project_id: str | None = None
    use_web: bool = False


class ChatResponse(BaseModel):
    answer: str
    used_rag: bool
    used_graph: bool
    used_web: bool
    # Non-fatal problems that happened while handling this request (e.g. a
    # downstream service was unreachable even after retries) — the request
    # still returns 200 with whatever context *did* come back, but the
    # caller can check this list to know something was missing.
    warnings: list[str] = []


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    decision = decide(req.message, use_web_flag=req.use_web)

    # Before running a full (expensive: embed + search + rerank) RAG query,
    # do a cheap existence check first — if literally nothing has been
    # ingested yet, there's no point calling query_documents() at all.
    # NOTE: this check is global across all users, not per-user — see the
    # big caveat comment in rag_client.has_documents() about why.
    use_rag = decision.use_rag
    if use_rag:
        try:
            use_rag = await rag_client.has_documents()
        except Exception as exc:
            # If we can't even tell whether documents exist, don't just
            # give up silently — fail open and let the real query attempt
            # (with its own retry) decide whether RAG is actually reachable.
            logger.warning("RAG existence check failed (%s); trying the real query anyway", exc)
            use_rag = True

    # Kick off every pipeline call we need at once (not one after another),
    # so if RAG takes 2s and the graph takes 1s, we wait ~2s total instead
    # of ~3s. asyncio.ensure_future schedules each coroutine to start
    # running immediately rather than waiting for something to await it.
    # Each call is wrapped in with_retry() (see retry.py) so a single slow
    # moment in a downstream service doesn't immediately count as a failure.
    tasks: dict[str, asyncio.Future] = {}
    if use_rag:
        tasks["rag"] = asyncio.ensure_future(
            with_retry(lambda: rag_client.query_documents(req.message), name="rag")
        )
    if decision.use_graph:
        tasks["graph"] = asyncio.ensure_future(
            with_retry(lambda: graph_client.get_graph(req.user_id, req.project_id), name="graph")
        )
    if decision.use_web:
        tasks["web"] = asyncio.ensure_future(
            with_retry(lambda: web_client.search(req.message), name="web")
        )

    # return_exceptions=True means if one call fails (e.g. connection
    # refused because a service is down, even after retries), asyncio.gather
    # doesn't raise and abort everything else — it puts the Exception object
    # in that slot of the results list instead, so the other calls can
    # still succeed.
    results = dict(zip(tasks.keys(), await asyncio.gather(*tasks.values(), return_exceptions=True)))

    context_parts = []
    warnings: list[str] = []

    for name, result in results.items():
        if isinstance(result, Exception):
            # Log it so it's visible in the server logs, record a
            # human-readable warning for the API caller, AND put an
            # explicit note directly in the LLM's prompt — so the model
            # itself knows context is missing and can say so in its
            # answer, instead of silently answering as if nothing failed.
            logger.warning("Pipeline %r failed after retries: %s", name, result)
            warnings.append(f"{name} lookup failed: {result}")
            context_parts.append(
                f"[{name} context unavailable — lookup failed after retries. "
                f"Mention in your answer that this information could not be checked.]"
            )
            continue
        if name == "rag":
            context_parts.append(f"Document context:\n{result.get('assembled_prompt', '')}")
        elif name == "graph":
            # An empty graph (e.g. a brand-new user, nothing extracted yet)
            # is a valid, successful result — not a failure — so it
            # doesn't get logged as a warning. It just doesn't add
            # anything useful to the prompt, so we skip including it.
            if result.get("nodes") or result.get("edges"):
                context_parts.append(f"Knowledge graph:\n{result}")
        elif name == "web":
            context_parts.append(f"Web search results:\n{result}")

    prompt = req.message + "\n\n" + "\n\n".join(context_parts)
    answer = await llm_client.generate(prompt) #give prompt to llm client to generate answer

    return ChatResponse(
        answer=answer,
        used_rag=use_rag,
        used_graph=decision.use_graph,
        used_web=decision.use_web,
        warnings=warnings,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}
