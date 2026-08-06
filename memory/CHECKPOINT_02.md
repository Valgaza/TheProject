# Checkpoint 02 — Memory Service Core

> **Model config update (2026-05-15):** LLM switched from Groq `llama-3.3-70b-versatile`
> to OpenAI `gpt-5.4-nano-2026-03-17`. Groq dependency removed entirely.
> Only `OPENAI_API_KEY` is required for both LLM inference and embeddings.

> **Bug fixes (2026-05-15):**
> - `reasoning="low"` now passed explicitly to `OpenAIClient` to override graphiti's
>   hardcoded `DEFAULT_REASONING = 'minimal'`, which `gpt-5.4-nano-2026-03-17` rejects.
> - Project-scoped `group_id` separator changed from `:` to `_` (e.g. `user_001_nexus`)
>   because graphiti's `validate_group_id` only allows `[a-zA-Z0-9_-]`.

## Testing Instructions

### Prerequisites

1. Neo4j must be running (see CHECKPOINT_01.md). Start it if needed:
   ```bash
   docker compose up -d
   # wait ~30 s for the health check to pass
   docker compose ps   # STATUS column should read "healthy"
   ```

2. A `.env` file must exist at the repo root with a real OpenAI API key:
   ```bash
   cp .env.example .env
   # fill in OPENAI_API_KEY — that is the only key the memory service needs
   ```

### Start the memory service

```bash
# From the repo root
cd memory
PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

Expected startup output:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Memory service ready
INFO:     Application startup complete.
```

You will also see a batch of `EquivalentSchemaRuleAlreadyExists` warnings from
Neo4j on every restart after the first. These are harmless — the indexes already
exist from the initial run and graphiti logs the conflict even though it uses
`IF NOT EXISTS`. Ignore them.

---

### Step 1 — Health check

Confirm the service is up and Neo4j is reachable before testing ingestion:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

**Expected response:**
```json
{
    "status": "ok",
    "neo4j": "ok"
}
```

If `neo4j` is `"unreachable"`, run `docker compose ps` and check the container
is healthy before proceeding.

---

### Step 2 — Ingest messages (user-level graph)

Send two messages for the same user. Each call returns immediately with HTTP 202;
the actual graph update happens in the background.

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Alice is a software engineer at Acme Corp. She works on the payments team.", "user_id": "user_001"}' \
  | python3 -m json.tool
```

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Alice is leading the migration to a new payment gateway called Stripe.", "user_id": "user_001"}' \
  | python3 -m json.tool
```

**Expected response for both (HTTP 202):**
```json
{
    "status": "accepted"
}
```

In the server log you should see the background task complete with a line like:
```
INFO:     Ingested episode for group=user_001
```
If you see `Failed to ingest episode` instead, check the traceback — it means
the LLM or embedding call failed (likely an API key or quota issue).

---

### Step 3 — Ingest a project-scoped message

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "The Nexus dashboard needs a dark mode toggle.", "user_id": "user_001", "project_id": "nexus"}' \
  | python3 -m json.tool
```

**Expected response (HTTP 202):**
```json
{
    "status": "accepted"
}
```

Server log should show:
```
INFO:     Ingested episode for group=user_001_nexus
```

Note the separator is `_`, not `:` — graphiti enforces alphanumeric/dash/underscore
only in group IDs.

---

### Step 4 — Retrieve the knowledge graph

Wait ~15–30 seconds after the last ingest for the background tasks to finish,
then fetch the graph:

```bash
# User-level graph (no project scope)
curl -s "http://localhost:8000/graph?user_id=user_001" | python3 -m json.tool
```

**Expected response:**
```json
{
    "group_id": "user_001",
    "nodes": [
        {
            "id": "a1b2c3d4-...",
            "name": "Alice",
            "type": "Entity",
            "summary": "Software engineer at Acme Corp on the payments team.",
            "attributes": {}
        },
        {
            "id": "e5f6g7h8-...",
            "name": "Acme Corp",
            "type": "Entity",
            "summary": "Company where Alice works.",
            "attributes": {}
        }
    ],
    "edges": [
        {
            "id": "i9j0k1l2-...",
            "source": "a1b2c3d4-...",
            "target": "e5f6g7h8-...",
            "label": "WORKS_AT",
            "fact": "Alice works at Acme Corp as a software engineer on the payments team."
        }
    ]
}
```

If `nodes` and `edges` are both `[]`, the background ingestion has not finished
yet — wait a few more seconds and retry.

```bash
# Project-scoped graph
curl -s "http://localhost:8000/graph?user_id=user_001&project_id=nexus" | python3 -m json.tool
```

**Expected response:**
```json
{
    "group_id": "user_001_nexus",
    "nodes": [
        {
            "id": "...",
            "name": "Nexus dashboard",
            "type": "Entity",
            "summary": "...",
            "attributes": {}
        }
    ],
    "edges": []
}
```

The user-level and project-level graphs are fully isolated — nodes from
`user_001` will not appear in `user_001_nexus` and vice versa.

---

### Interactive API docs

```
http://localhost:8000/docs
```

---

## Context Summary

### What was built

| File | Purpose |
|------|---------|
| `memory/app/__init__.py` | Makes `app/` a Python package |
| `memory/app/config.py` | Reads env vars at startup; raises `RuntimeError` for any missing required var |
| `memory/app/main.py` | FastAPI application: lifespan, models, and all three routes |

### How Graphiti is configured

| Concern | Choice |
|---------|--------|
| LLM | OpenAI — `gpt-5.4-nano-2026-03-17`, `reasoning="low"` |
| Embedder | OpenAI — `text-embedding-3-small` (1536-dim) |
| Cross-encoder | OpenAI reranker (graphiti default; used during search, not yet exposed) |
| Graph driver | Neo4j via bolt URI |

Both LLM and embedder authenticate with `OPENAI_API_KEY`. `GROQ_API_KEY` is no
longer read by the memory service.

`build_indices_and_constraints()` runs on every startup. It is idempotent — safe
to call repeatedly. Ignore the `EquivalentSchemaRuleAlreadyExists` log lines it
produces after the first run.

### Graph scoping (group_id)

Graphiti partitions its graph by a `group_id` string. Only `[a-zA-Z0-9_-]` is
allowed. The service constructs it as:

- User-only: `group_id = user_id`  (e.g. `user_001`)
- User + project: `group_id = "{user_id}_{project_id}"`  (e.g. `user_001_nexus`)

### Endpoint reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness + Neo4j reachability |
| `POST` | `/ingest` | Accept a message for async graph ingestion (HTTP 202) |
| `GET` | `/graph` | Return nodes + edges for a user/project scope |

### Request / Response shapes

**POST `/ingest`**
```
Request  { "text": str, "user_id": str, "project_id": str | null }
Response { "status": "accepted" }   HTTP 202
```

**GET `/graph?user_id=&project_id=`**
```
Response {
  "group_id": str,
  "nodes": [{ "id", "name", "type", "summary", "attributes" }],
  "edges": [{ "id", "source", "target", "label", "fact"     }]
}
```

**GET `/health`**
```
Response { "status": "ok" | "degraded" | "starting", "neo4j": "ok" | "unreachable" | "unknown" }
```

### Current project state

```
TheProject/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .venv/
├── frontend/
├── memory/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   └── main.py
│   ├── pyproject.toml
│   ├── CHECKPOINT_01.md
│   └── CHECKPOINT_02.md
├── pipelines/
└── shared/
```
