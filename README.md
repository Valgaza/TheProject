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

A chat does not have to belong to a project. One that does not reads and writes
the user tier alone, which is what a passing question should touch — filing it
under a project would write into that body of work.

`/graph` draws one tier at a time: the universal graph, one project's, or the
union (`?view=user|project|all`). Drawing them as a single cloud is what makes
it impossible to see which tier a fact actually landed in, and that is the thing
most worth checking.

### Memory is written carefully and tidied afterwards

The write gate only learns from what *you* said. The assistant's reply is passed
to it as context, so it can resolve references, but nothing is persisted on its
authority — otherwise asking "what am I using for the graph store?" gets the
answer "Neo4j" and writes that answer back as a new fact, which is a loop that
makes a stale fact louder every time you ask about it. Facts memory already
supplied for the same turn are named in the gate prompt and refused.

One turn can leave several facts behind, in different tiers, capped at three.

What cannot be prevented at write time gets repaired by a maintenance pass:
duplicate and restated edges collapse, node summaries are rebuilt from the facts
of their live edges, repeated episodes stop reaching search, and orphans go. It
marks rather than deletes, never crosses a group boundary, makes no model calls,
and reports before it acts. `POST /maintenance` runs it; the graph panel has a
button; and a tier is swept automatically once it has taken ten new episodes.

Each step's reasoning is in `memory/nexus_memory/hygiene.py`, alongside the
measurements that motivated it.

## Containers

Four containers, all declared in the root `docker-compose.yml`, all started by
one `docker compose up -d`.

| Container | Port | What it does |
|---|---|---|
| `nexus-postgres` | 54322 | Postgres 17. Chats, messages, projects — what was said. |
| `nexus-neo4j` | 7474, 7687 | The knowledge graph — what was learned. Browser UI on 7474, bolt on 7687. |
| `nexus-qdrant` | 6333, 6334 | Document vectors, backing `rag/rag_pipeline`. |
| `nexus-searxng` | 8080 | Private metasearch, so web queries never hit a third-party search API. Not used by anything yet — see the RAG/orchestrator section below. |

Only the databases are containerised. The application code (`core`, `memory`,
the RAG API, the frontend) runs directly on the host during development, so
editing a line doesn't mean rebuilding an image. Dockerfiles for those come
when we deploy.

**Postgres replaced the Supabase CLI on 2026-09-02.** The CLI started twelve
containers (Auth, Storage, Realtime, Studio, an email catcher, log shipping…)
for the one Postgres this project actually uses. The schema in
`db/migrations/` is plain Postgres — no Supabase-specific roles or
policies — so it applies unchanged to a stock `postgres:17` image, and
`db/` is the source of truth for it. What's gone is Studio (the
table-browsing web UI) and `supabase db reset`; see below for the replacement.

> ⚠️ **`docker compose down -v` now wipes every database at once** — graph,
> vectors and chat history together. Plain `docker compose down` (no `-v`)
> stops the containers and keeps the data.

To reset **only** Postgres — the `supabase db reset` equivalent, re-applying
migrations and the seed from scratch:

```bash
docker compose rm -sf postgres
docker volume rm nexus_postgres_data
docker compose up -d postgres
```

The schema is applied by `docker/postgres-init.sh`, which the `postgres` image
runs automatically the first time the data volume is created. It loops over
`db/migrations/*.sql` in filename order, so a new migration added later
needs no change to the compose file or the script.

To browse tables without Studio:

```bash
docker exec -it nexus-postgres psql -U postgres -d postgres
```

## Setup

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/), and Node 20.9+ (Next 16
requires it). Nothing else is installed on your machine — the databases all run
in containers.

```bash
cp .env.example .env      # fill in OPENAI_API_KEY and NEO4J_PASSWORD
uv sync                   # installs core, memory and the RAG pipeline editable
cd frontend && npm install && cd ..
```

Then, from the repo root:

```bash
./start.sh                     # containers + memory (8000) + RAG API (8001) + core (8100)
cd frontend && npm run dev     # separate terminal
```

Open http://localhost:3000/chat. Ctrl+C stops the Python services and leaves the
containers running.

`start.sh` starts the containers itself and then waits for Neo4j and Postgres
before launching anything, because `core` is useless without them and the
failure is otherwise a wall of asyncpg traceback thirty seconds after
everything looked fine.

To run a service by hand, from the repo root:

```bash
uv run uvicorn nexus_memory.main:app --reload --port 8000
uv run uvicorn rag_api:app --app-dir rag --reload --port 8001
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
db/migrations/          the schema, source of truth for what Postgres holds
tests/                  end-to-end scenarios (opt-in, drives the live stack)
pipelines/, shared/     empty; the original plan for where RAG and vision
                        would land — see below, that's not where they landed
rag/, orchestrator/     the RAG pipeline (rag/rag_api.py is its HTTP front
                        door, called by core) and a standalone orchestrator
                        scaffold — built on a separate branch, merged in
                        below `pipelines/`'s intended spot
```

Each service owns its tests: `core/tests/` and `memory/tests/`. `rag/` owns its
own (`rag/tests/`).

The two Python packages are named `nexus_core` and `nexus_memory` rather than
`app`. They were both `app` once, installed into the same virtualenv, which
meant `import app` resolved to whichever won and the other was unreachable.

## RAG pipeline — merged 2026-08-31, wired into `core` 2026-09-01

`rag/` was built on `feature/rag-pipeline`, in parallel with everything
above, and landed here through a git merge rather than through the
`pipelines/`/`shared/` seam this README describes. It's now a real `uv`
workspace member (same `uv sync`, same shared `.venv` as `core`/`memory` —
no longer a separate install), and `core`'s `select_route()` calls it for
real: a project with ingested documents routes to `"rag"`, and the retrieved
context folds into the answer.

| Service | Location | Port | Depends on |
|---|---|---|---|
| RAG API | `rag/rag_api.py`, backed by `rag/rag_pipeline/` | `8001` | Qdrant |
| Orchestrator (scaffold, standalone) | `orchestrator/` | `8002` | RAG API, `memory`, SearXNG |

`rag/rag_api.py` is RAG's own HTTP front door — the only way `core` can
reach it, since they're separate processes (see
[`docs/RAG_API_SERVICE.md`](docs/RAG_API_SERVICE.md) for the
full "why is this a separate service" writeup; it's not a user-facing
surface, nothing outside `core` is meant to call it). Fully local — no cloud
LLM calls anywhere in `rag/rag_pipeline`.

**Two orchestrators still exist**, doing overlapping jobs: `core`'s
`select_route()` (above, now doing real work) and the standalone
`orchestrator/`'s own router (still just a scaffold — its LLM client is a
stub, nothing calls it in production). Plan is to port `orchestrator/`'s
useful pieces (its web-search/SearXNG client, its retry logic) into `core`
and then retire it — see `steps-to-implement-Bhoumish.md` (gitignored, ask
whoever's working on the orchestrator for the current state).
[`docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`](docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md)
tracks open product/safety decisions the same way `RUNBOOK.md` tracks
operational ones.

Nothing extra to start: Qdrant and SearXNG are both in the root
`docker-compose.yml`, and `./start.sh` runs the RAG API alongside `core` and
`memory`. `rag/docker-compose.yml` and `orchestrator/docker-compose.yml` were
folded into the root file on 2026-09-02 and deleted.

The standalone `orchestrator/` scaffold, if you want to poke at it, is the one
thing still started by hand:

```bash
cp orchestrator/.env.example orchestrator/.env
pip install -e "orchestrator"
uvicorn app.main:app --reload --port 8002 --app-dir orchestrator
```

The first RAG query after a cold start downloads embedding/reranker models
from the internet and caches them locally — after that, no internet access is
needed to use the pipeline.

## Tests

```bash
uv run pytest                            # core + memory: ~110 tests, ~2s, no services needed
RUN_SCENARIOS=1 uv run pytest tests -v   # end-to-end, ~2 min, spends API credit
uv run pytest rag/tests                  # RAG pipeline's own suite
```

The scenario suite is the one that answers whether memory works: a fact stated
in one chat recalled in another, a project fact staying out of other projects, a
personal fact following you between them, and chit-chat writing nothing.

## Further reading

[`RUNBOOK.md`](RUNBOOK.md) covers day-to-day operation and the failures that
cost time in practice — stale Turbopack caches, ports that will not release, and
memory that looks like it forgot something but is twenty seconds into
extraction.

`docs/history/CHECKPOINT_0*.md` are historical records of earlier states. They describe
a layout that no longer exists and are kept for the reasoning, not the
instructions.
