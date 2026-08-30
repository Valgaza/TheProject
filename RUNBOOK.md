# Nexus — running it locally

Current as of the memory milestone. `memory/CHECKPOINT_0*.md` are historical
records of earlier states and describe a layout that no longer exists; this is
the file to trust.

## What runs where

| Piece | Port | What it is |
|---|---|---|
| Neo4j | 7474 / 7687 | The knowledge graph. One Docker container. |
| Supabase stack | 54321-54324 | Postgres, Studio, auth, storage. Eleven containers. |
| `nexus_memory` | 8000 | Owns the graph: ingest, search, live updates. |
| `nexus_core` | 8100 | Owns chat, the orchestrator, providers, and Postgres. |
| Frontend | 3000 | Next.js. |

Two databases, two jobs: **Postgres stores what was said**, every message,
always. **Neo4j stores what was learned**, gated. Neither is a cache of the
other.

All containers run under one Docker Compose project named `nexus`, so Docker
Desktop shows them as a single group.

## First-time setup

```bash
cp .env.example .env          # then fill in OPENAI_API_KEY and NEO4J_PASSWORD
uv sync                       # installs both services editable
cd frontend && npm install && cd ..
```

## Cold start

```bash
supabase start                # once; the CLI owns these containers
supabase db reset             # first run only: migrations + seed.sql

./start.sh                    # Neo4j + memory (8000) + core (8100)
cd frontend && npm run dev    # separate terminal
```

`start.sh` checks Supabase is up and tells you if it is not, rather than letting
the core service fail with a wall of asyncpg traceback. Ctrl+C stops both
services; Neo4j keeps running.

To run a service by hand instead:

```bash
uv run uvicorn nexus_memory.main:app --reload --port 8000
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
docker compose down            # Neo4j only -- it is the only service declared
                               # in docker-compose.yml
supabase stop                  # the Supabase containers, which the CLI owns
```

`docker compose down` genuinely does not touch Supabase, despite the shared
project name. The two are managed by different tools.

Neo4j's volumes are named explicitly, so they survive. Supabase's are not:
`supabase stop --no-backup` loses the database, and `supabase db reset` re-runs
migrations and the seed from scratch.

## Things that will bite you

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
