# `rag/rag_api.py` — what it is and what it does

## What it is

`rag/rag_api.py` is a small FastAPI service that exposes `rag/rag_pipeline`
over HTTP. It's RAG's own front door — the file a separate process (`core`)
actually connects to, since two separate running programs can only talk to
each other over a network, never by calling each other's functions directly.

Formerly lived at `controller/main.py`, in its own top-level folder — moved
and renamed 2026-09-02. "Controller" was a misleading name (it sounds like
"the central brain," which is actually `core`'s job) for what's really just
RAG's receiving end. Nothing about how it runs changed: still its own
process, still port 8001, still exactly the same four endpoints.

## What it actually does, endpoint by endpoint

| Endpoint | What it does |
|---|---|
| `POST /api/ingest` | Accepts an uploaded PDF/Markdown file, writes it to a temp path, and runs it through `rag/rag_pipeline`'s `IngestionPipeline` (load → chunk → embed → upsert into Qdrant). |
| `POST /api/query` | Runs a question through `QueryPipeline` (embed → hybrid search → rerank → assemble context) and returns the assembled prompt + source metadata. |
| `GET /api/status` | Cheap collection point-count, no embedding/search/rerank. Used by `core/nexus_core/rag_client.py`'s `has_documents()`. |
| `GET /api/health` | Plain liveness check. |

It contains no routing logic, no LLM calls, and no memory integration — it is
purely a network-facing shim around the RAG pipeline's Python classes.

## Why it exists as a separate service instead of being part of `core`

`core` and `rag/rag_pipeline` are two different running programs (processes),
each with its own private memory, isolated from each other by the operating
system the same way any two programs on your machine are. The only way two
separate processes can exchange information is over a network connection —
which is what `core/nexus_core/rag_client.py` does, and what this file exists
to answer.

The alternative — `core` importing `rag_pipeline` directly and running it in
the same process — was considered and deliberately not done (2026-09-01):
it would drop `rag_client.py`'s whole reason to exist (a swappable HTTP
client, no longer needed with no network hop to make), pull RAG's heavy ML
dependencies (torch, transformers) into `core`'s own dependency list, and
break the pattern every other pipeline (vision, speech) will need to follow
anyway — those will be separate services too, likely in different tech
stacks entirely, and can't be merged into `core`'s process even if wanted.
Keeping RAG as its own service over HTTP keeps every pipeline connecting to
`core` the same way, not one special-cased exception.
