# Nexus

A local multi-pipeline AI orchestration system. One composer box, an
orchestrator that decides internally which pipeline should handle what you
typed, and a memory layer wrapped around all of it that builds itself from your
conversations.

You never pick a pipeline. You type, or attach a document, or speak, and the
system works out what to do with it.

Four pipelines are planned: document Q&A over retrieval, OCR and vision,
conversational speech, and general chat. General chat and the memory layer are
built; the other two are being developed in parallel and attach to the
orchestrator seam described below.

## How it fits together

Two backend services, deliberately split by what they own:

```
                    frontend :3000
                          │
                          ▼
        ┌──── core :8100 ────────────────────┐
        │  chat, orchestration, providers    │
        │  owns Postgres                     │
        └──────┬──────────────────┬──────────┘
               │                  │
               ▼                  ▼
      Postgres (Supabase)   memory :8000
      what was said          what was learned
                                  │
                                  ▼
                              Neo4j
```

**Two databases, two jobs.** Postgres stores every message, always. Neo4j stores
distilled facts, and only when a write gate decides something is worth keeping.
Neither is a cache of the other, and losing the graph would cost you memory but
not history.

`core` talks to `memory` over HTTP and owns no graph of its own. `memory` knows
nothing about chats or projects beyond the scope ids it is handed. That split is
what lets the memory service stay a single-purpose thing while the orchestrator
grows.

### The orchestrator seam

`core/nexus_core/orchestrator.py` is where the pipelines attach.
`select_route(content, attachments)` has one branch today and always returns
`"general"`, but the signature is the real one, including an `Attachment` type
for the file, image and audio cases. RAG and vision land as extra branches
rather than as a rewrite of the caller.

### Memory has two tiers

A **user** scope holds what stays true across everything you do: who you are,
what you prefer, how you work. A **project** scope holds what is only true of one
body of work. A read unions both; a write picks exactly one.

That asymmetry is not a preference. graphiti's read paths take a list of scopes
and rank across them, while `add_episode` takes exactly one — so the write gate
has to *choose* a tier rather than write to both and sort it out later.

The practical effect: a technology decision in one project is invisible from
another, while "I prefer terse answers" follows you everywhere.

## Containers

Thirteen containers across two Docker Compose projects, both named `nexus` so
Docker Desktop groups them together. Only one of them is ours.

| Container | Port | What it does |
|---|---|---|
| `nexus-neo4j` | 7474, 7687 | The knowledge graph. Browser UI on 7474, bolt on 7687. |

The other twelve come from `supabase start` and are managed by the Supabase CLI,
not by `docker-compose.yml`:

| Container | Port | What it does |
|---|---|---|
| `supabase_db_nexus` | 54322 | Postgres 17. The one that matters — chats, messages, projects. |
| `supabase_kong_nexus` | 54321 | API gateway; the `SUPABASE_URL` everything else goes through. |
| `supabase_studio_nexus` | 54323 | Web UI for browsing tables. Useful for watching rows land. |
| `supabase_rest_nexus` | — | PostgREST. Unused: `core` talks to Postgres directly over SQL. |
| `supabase_auth_nexus` | — | GoTrue. Unused until auth lands; the schema is already shaped for it. |
| `supabase_realtime_nexus` | — | Postgres change streams. Unused. |
| `supabase_storage_nexus` | — | File storage. Unused. |
| `supabase_inbucket_nexus` | 54324 | Catches outbound email in development. Unused. |
| `supabase_analytics_nexus` | 54327 | Log aggregation. |
| `supabase_vector_nexus` | — | Log shipping for the above. |
| `supabase_pg_meta_nexus` | — | Backs Studio's table browser. |
| `supabase_edge_runtime_nexus` | — | Deno functions. Not used, and normally sits exited. |

Most of that stack is idle. It comes as one unit and the local CLI does not offer
a smaller version, which is a fair trade for a Postgres that matches production
and applies migrations the same way.

**`docker compose down` only stops Neo4j.** It scopes to services declared in
`docker-compose.yml`, and the Supabase containers are the CLI's. Use
`supabase stop` for those.

## Setup

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 20.9+ (Next 16
requires it), and the
[Supabase CLI](https://supabase.com/docs/guides/local-development).

```bash
cp .env.example .env      # fill in OPENAI_API_KEY and NEO4J_PASSWORD
uv sync                   # installs both services editable
cd frontend && npm install && cd ..

supabase start            # first run pulls a lot of images
supabase db reset         # applies migrations and seeds the demo user
```

Then, from the repo root:

```bash
./start.sh                     # Neo4j + memory (8000) + core (8100)
cd frontend && npm run dev     # separate terminal
```

Open http://localhost:3000/chat. Ctrl+C stops both Python services and leaves
Neo4j running.

`start.sh` checks Supabase is up before starting anything, because `core` is
useless without Postgres and the failure is otherwise a wall of asyncpg
traceback thirty seconds after everything looked fine.

To run a service by hand, from the repo root:

```bash
uv run uvicorn nexus_memory.main:app --reload --port 8000
uv run uvicorn nexus_core.main:app   --reload --port 8100
```

Through `uv run` or `.venv/bin/uvicorn` — a plain `uvicorn` outside the venv is
a different interpreter with none of this installed.

### Local inference

Deferred on purpose. The provider layer
(`core/nexus_core/providers/`) is the only thing that talks to a model, and
every model name comes from the environment. Pointing the whole system at an
OpenAI-compatible local server is `OPENAI_BASE_URL` plus the model names, not a
refactor.

## Repository layout

```
core/nexus_core/        chat, orchestration, providers, the write gate
memory/nexus_memory/    the graph: scopes, ingest, retrieval, live updates
frontend/               Next.js 16, hand-written CSS, no UI dependencies
supabase/migrations/    the schema, source of truth for what Postgres holds
tests/                  end-to-end scenarios (opt-in, drives the live stack)
pipelines/, shared/     empty; where the RAG and vision work will land
```

Each service owns its tests: `core/tests/` and `memory/tests/`.

The two Python packages are named `nexus_core` and `nexus_memory` rather than
`app`. They were both `app` once, installed into the same virtualenv, which
meant `import app` resolved to whichever won and the other was unreachable.

## Tests

```bash
uv run pytest                            # ~110 tests, ~2s, no services needed
RUN_SCENARIOS=1 uv run pytest tests -v   # end-to-end, ~2 min, spends API credit
```

The scenario suite is the one that answers whether memory works: a fact stated
in one chat recalled in another, a project fact staying out of other projects, a
personal fact following you between them, and chit-chat writing nothing.

## Further reading

[`RUNBOOK.md`](RUNBOOK.md) covers day-to-day operation and the failures that
cost time in practice — stale Turbopack caches, ports that will not release, and
memory that looks like it forgot something but is twenty seconds into
extraction.

`memory/CHECKPOINT_0*.md` are historical records of earlier states. They describe
a layout that no longer exists and are kept for the reasoning, not the
instructions.
# NexusAI

A locally-run AI assistant for institutions that can't send client data to the cloud (medical practices, law firms). Documents and conversations stay on-machine; retrieval, memory, and (eventually) generation all run against local infrastructure.

The system has two memory subsystems that feed context to an LLM:

- **RAG pipeline** — documents (PDF/Markdown) get chunked, embedded, and stored in a vector database, so relevant passages can be retrieved for a question.
- **Knowledge graph** — entities and relationships extracted from conversations, for information that doesn't fit well into a vector search (e.g. "who reports to whom," "which case cites which precedent").

See [`.github/PROJECT_STATUS_REPORT.md`](.github/PROJECT_STATUS_REPORT.md) for the current architecture snapshot, known issues, and open decisions — that document goes deeper than this README and is the place to check for what's unresolved.

## Services

| Service | Location | Port | Depends on |
|---|---|---|---|
| Frontend (Next.js) | `frontend/` | `3000` | Memory service, RAG controller |
| Knowledge graph memory service (FastAPI) | `memory/` | `8000` | Neo4j, OpenAI (see status report — planned to move local) |
| RAG controller (FastAPI) | `controller/`, backed by `RAG/rag_pipeline/` | `8001` | Qdrant |
| Orchestrator (FastAPI) | `orchestrator/` | `8002` | Memory service, RAG controller, SearXNG. Early scaffold — see `orchestrator/README.md`. |

## Prerequisites

- Node.js (for the frontend)
- Python 3.12+
- Docker (for Neo4j and Qdrant, via `docker-compose`)

## Running everything

Each service is started separately today — there's no single script yet that boots all of them (see status report's "Recommended Next Steps").

### 1. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 2. Knowledge graph memory service

From the repository root:

```bash
cp .env.example .env
```

Fill in the required keys in `.env` (Neo4j credentials, `OPENAI_API_KEY`), then:

```bash
./start.sh
```

This starts Neo4j via Docker Compose and runs the memory service on `http://localhost:8000`.

### 3. RAG service

From the repository root:

```bash
cp RAG/env.example RAG/.env
docker compose -f RAG/docker-compose.yml up -d
python -m venv .venv
source .venv/bin/activate
pip install -e "RAG[dev]"
pip install -r controller/requirements.txt
uvicorn controller.main:app --reload --port 8001
```

This starts Qdrant via Docker Compose and runs the RAG controller on `http://localhost:8001`.

Note: the first run of the RAG pipeline (ingesting or querying) downloads embedding and reranker models from the internet and caches them locally. After that first download, no internet access is required to use the pipeline.

### 4. Orchestrator

From the repository root:

```bash
cp orchestrator/.env.example orchestrator/.env
docker compose -f orchestrator/docker-compose.yml up -d
source .venv/bin/activate
pip install -e "orchestrator"
uvicorn app.main:app --reload --port 8002 --app-dir orchestrator
```

This starts SearXNG (self-hosted web search) via Docker Compose and runs the orchestrator on `http://localhost:8002`. See `orchestrator/README.md` — it's an early scaffold, not wired to a real LLM yet.

## Frontend environment variables

The frontend reads these at build time (see `frontend/src/app/demo/page.tsx`, `conversation/page.tsx`, `rag-demo/page.tsx`, `live-graph.tsx`):

```bash
NEXT_PUBLIC_MEMORY_URL=http://localhost:8000   # defaults to this if unset
NEXT_PUBLIC_RAG_URL=http://localhost:8001      # defaults to this if unset
```

No committed template file for these exists yet — set them in a local, git-ignored `frontend/.env.local` if you need to point at non-default URLs.

## Tests

```bash
python -m pytest RAG/tests
```
