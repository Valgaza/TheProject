# Nexus — running it locally

Current as of the memory milestone. `memory/CHECKPOINT_0*.md` are historical
records of earlier states and describe a layout that no longer exists; this is
the file to trust.

## What runs where

| Piece | Port | What it is |
|---|---|---|
| Postgres | 54322 | Chats, messages, projects. One Docker container. |
| Neo4j | 7474 / 7687 | The knowledge graph. One Docker container. |
| Qdrant | 6333 / 6334 | Document vectors for RAG. One Docker container. |
| SearXNG | 8080 | Private metasearch. Not wired into `core` yet. |
| `nexus_memory` | 8000 | Owns the graph: ingest, search, live updates. |
| RAG API | 8001 | `RAG/rag_api.py` — the only way `core` reaches the RAG pipeline. |
| `nexus_core` | 8100 | Owns chat, the orchestrator, providers, and Postgres. |
| Frontend | 3000 | Next.js. |

Two databases, two jobs: **Postgres stores what was said**, every message,
always. **Neo4j stores what was learned**, gated. Neither is a cache of the
other. Qdrant is a third store with a narrower job: the chunks of uploaded
documents, searched by the RAG pipeline.

All four containers are declared in the root `docker-compose.yml` and start
together with one `docker compose up -d` (which `./start.sh` runs for you).

## First-time setup

```bash
cp .env.example .env          # then fill in OPENAI_API_KEY and NEO4J_PASSWORD
uv sync                       # installs both services editable
cd frontend && npm install && cd ..
```

## Cold start

```bash
./start.sh                    # containers + memory (8000) + RAG API (8001) + core (8100)
cd frontend && npm run dev    # separate terminal
```

`start.sh` brings the containers up itself and waits for Neo4j and Postgres
before launching anything, rather than letting the core service fail with a
wall of asyncpg traceback. Ctrl+C stops the Python services; the containers
keep running.

The Supabase CLI is no longer used (replaced 2026-09-02) — Postgres is just
another container in `docker-compose.yml`, and the schema in
`supabase/migrations/` is applied automatically the first time its volume is
created. Do **not** run `supabase start`: it would try to bind port 54322 and
collide with the Postgres container.

To run a service by hand instead:

```bash
uv run uvicorn nexus_memory.main:app --reload --port 8000
uv run uvicorn rag_api:app --app-dir RAG --reload --port 8001
uv run uvicorn nexus_core.main:app   --reload --port 8100
```

**From the repo root, and through `uv run` or `.venv/bin/uvicorn`.** No
`PYTHONPATH`, no `cd core`, and not the system `uvicorn` — that is a different
interpreter with none of this installed.

The old command `uvicorn app.main:app` no longer works. Both services used to
ship a top-level package called `app`, so whichever was imported first won and
the other was unreachable. They are `nexus_memory` and `nexus_core` now.

## Checking it works

```bash
curl -s localhost:8000/health   # {"status":"ok","neo4j":"ok"}
curl -s localhost:8100/health   # provider, chat_model, memory_service, database
```

`core`'s health reports `memory_service` and `database` separately, and always
returns 200 — read the fields, not the status code.

Then open http://localhost:3000/chat and say something. Supabase Studio is at
http://localhost:54323 if you want to watch rows land.

## Tests

```bash
uv run pytest                  # ~110 unit tests, ~2s, no services needed
                               # (database-backed ones skip if Supabase is down)

RUN_SCENARIOS=1 uv run pytest tests -v
                               # end-to-end scenarios: needs all services up,
                               # spends API credit, takes ~2 minutes
```

The scenario suite is the one that answers whether memory actually works:
cross-chat recall, cross-project isolation, personal facts following you between
projects, and the write gate keeping chit-chat out of the graph.

## Stopping

```bash
docker compose down            # all four containers; volumes survive
```

**Never `docker compose down -v` unless you mean it.** Since the compose files
were merged (2026-09-02), `-v` deletes every volume at once: the graph, the
document vectors and the chat history together.

Every volume is named explicitly (`nexus_postgres_data`, `nexus_neo4j_data`,
`nexus_neo4j_logs`, `nexus_qdrant_storage`), so a plain `down` keeps all of it.

To reset one database without touching the others — Postgres shown here, the
same shape works for the rest:

```bash
docker compose rm -sf postgres
docker volume rm nexus_postgres_data
docker compose up -d postgres     # re-applies migrations + seed.sql
```

## Things that will bite you

**"Required environment variable 'OPENAI_API_KEY' is not set" — but it IS set
in the root `.env`.** A stray `.env` in a subdirectory is shadowing it. Bare
`load_dotenv()` searches *upward* from the calling file and stops at the first
`.env` it finds, so `core/.env` wins over the repo-root `.env` for anything
under `core/` — and if that stray file defines the key as empty, you get "not
set" while the real value sits two directories up. `core/nexus_core/config.py`
now loads the repo-root path explicitly and is immune; `memory/` still uses
bare `load_dotenv()`, so a `memory/.env` would do the same thing to it. Check
with `find . -name .env -not -path "*/node_modules/*" -not -path "*/.venv/*"`.

**Frontend 404s on every route while Next says "Ready".** Stale Turbopack cache.
`rm -rf frontend/.next frontend/tsconfig.tsbuildinfo`.

**A service will not stop with Ctrl-C.** `lsof -ti:8100 | xargs kill -9`.

**Neo4j logs flood the terminal.** They should not — `neo4j.notifications` is
pinned to ERROR because graphiti raises a 01N52 notification on every ingest, at
WARNING, roughly 1.5KB a line. If you need them: `docker logs nexus-neo4j`.

**`uv sync` uninstalls the services.** Fixed — they are declared as workspace
dependencies of the root project now. If it recurs, check `[tool.uv.sources]` in
the root `pyproject.toml`.

**Memory seems to forget something you just told it.** Extraction takes 12-30s
and runs in the background after the reply. Check
`GET /chats/{chat_id}/memory-episodes` for what the write gate decided, and the
core log for `gate persisted` / `gate declined` lines with reasons.

**Changing `EMBEDDING_DIM` invalidates every stored vector.** Similarity is only
meaningful between vectors of the same length. It is 1536; leave it.
