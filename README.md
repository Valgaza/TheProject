# Nexus

A local multi-pipeline AI orchestration system. One composer box, an
orchestrator that decides internally which pipeline should handle what you
typed, and a memory layer wrapped around all of it that builds itself from your
conversations.

You never pick a pipeline. You type, or attach a document, or speak, and the
system works out what to do with it.

Four kinds of input: documents, images, conversational speech, and general
chat. All four are built, along with the memory layer and opt-in web search,
and run entirely on local models.

## How it fits together

```
                       frontend :3000
                             │   session cookie; voice over WebRTC
                             ▼
        ┌──────────────── core :8100 ─────────────────┐
        │  auth, chats, projects, documents, routing,  │
        │  prompt assembly, the memory write queue,    │
        │  voice sessions (Pipecat, Smart Turn)        │
        └───┬──────────────┬──────────────┬───────┬────┘
            │              │ service token│       │      only when asked
            ▼              ▼              ▼       ▼
        Postgres      memory :8000    RAG API   websearch :8004 ─▶ SearXNG
        what was      the graph       :8001     redacts, checks,   (no host
        said          (Neo4j)         (Qdrant)  audits             port)

        Every model runs in runtime :8003 (MLX): the chat LLM, embeddings,
        the decision model (Laya), speech-to-text and text-to-speech, behind
        one priority queue so a live answer always goes first.
```

The browser talks to `core` and nothing else. `memory` and the RAG API refuse
any request without core's `SERVICE_TOKEN`, so every read of the graph or the
documents goes through a check of who is signed in.

**Two databases, two jobs.** Postgres stores every message, always. Neo4j stores
distilled facts, and only when a write gate decides something is worth keeping.
Neither is a cache of the other, and losing the graph would cost you memory but
not history.

`core` talks to `memory` over HTTP and owns no graph of its own. `memory` knows
nothing about chats or projects beyond the scope ids it is handed. That split is
what lets the memory service stay a single-purpose thing while the orchestrator
grows.

### The orchestrator seam

`core/nexus_core/orchestrator.py` decides which handler answers a turn, and is
where vision and speech will attach. It is deliberately not a model call: cheap
signals decide. Today it picks between `general` and `web`. Documents are not a
route: every turn retrieves from them, and a relevance floor decides per turn
whether anything retrieved reaches the prompt.

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

### Documents follow the same tiers

An uploaded file is a Postgres record owned by you and, optionally, a project;
its chunks live in Qdrant tagged with the tier they belong to. A question inside
a project reads that project's documents and your universal ones; a question
outside every project reads only the universal ones. The rule is
`rag/rag_pipeline/scopes.py`, and it mirrors memory's on purpose. Uploading also
tells the graph, in at most three facts, what the document is about, so memory
knows you have something on a topic without holding a lossy copy of it.

### Web search is the one thing that leaves the machine

It runs only when you ask: the composer's globe button, saying so in words
("look it up", "check online"), or saying yes when Nexus offers to check.
Core hands the message to the websearch container, the only component with a
route to SearXNG. There a model rewrites it into a generic topic query, your
own names, email addresses and long ID-like numbers are stripped from the
result, and the decision model checks it no longer identifies anyone; if any
step fails, nothing is sent. Every query that leaves is written to an audit
log, and shown on the message. Why it works this way: flag 1 in
[`docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`](docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md).

### Talking to it

The mic button opens a hands-free conversation in the same chat, over WebRTC:
speak, pause, and the answer is spoken back a piece at a time while the rest
is still being written. Smart Turn listens to how you said something to tell a
finished sentence from a pause. Talk over it and it stops, keeping only what
you heard; "mm-hmm" doesn't stop it. Ask it to look something up and it says
so first, then searches. Voice turns are ordinary messages, with the same
memory, documents and web.

### Looking at images

Attach, paste or drop an image into the composer and ask about it: a photo, a
screenshot, a chart, a receipt. A separate vision model (Qwen3.5-4B) answers
that turn, with the same memory and documents as any other; it loads when the
image is attached and unloads when idle. The question straight after sees the
image again, and later turns know it by a caption the vision model writes, so
"what was the total on that receipt?" works by voice too. Images stay on the
machine, stripped of location data, and go when their chat is deleted.

### Decisions that do not need an LLM

Several choices in a turn are classifications: is this worth remembering, is
it asking for the web, does this query still identify someone.
Laya, a small decision model in the runtime, answers them in milliseconds.
Each one runs in shadow first, logged beside the existing logic, and goes live
only after measuring well on hand-labelled examples (`RUNBOOK.md`).

## Containers

Five containers, all declared in the root `docker-compose.yml`, all started by
one `docker compose up -d`.

| Container | Port | What it does |
|---|---|---|
| `nexus-postgres` | 54322 | Postgres 17. Chats, messages, projects, document records — what was said. |
| `nexus-neo4j` | 7474, 7687 | The knowledge graph — what was learned. Browser UI on 7474, bolt on 7687. |
| `nexus-qdrant` | 6333, 6334 | Document vectors, backing `rag/rag_pipeline`. |
| `nexus-searxng` | none | Private metasearch, so queries never hit a third-party search API. No host port. Config in `docker/searxng/`. |
| `nexus-websearch` | 8004 (localhost) | `websearch/`: the only caller of SearXNG. Redacts, checks and audits every query. |

The databases are containerised, and so is websearch, so that the one path to
the internet sits on its own network beside SearXNG. The rest of the
application code (`core`, `memory`, the RAG API, the runtime, the frontend)
runs directly on the host during development, so editing a line doesn't mean
rebuilding an image; the runtime has to, since MLX needs the Mac's GPU.

**Postgres replaced the Supabase CLI on 2026-09-02.** The CLI started twelve
containers (Auth, Storage, Realtime, Studio, an email catcher, log shipping…)
for the one Postgres this project actually uses. The schema in
`db/migrations/` is plain Postgres — no Supabase-specific roles or
policies — so it applies unchanged to a stock `postgres:17` image, and
`db/` is the source of truth for it.

> ⚠️ **`docker compose down -v` wipes every database at once** — graph,
> vectors and chat history together. Plain `docker compose down` (no `-v`)
> stops the containers and keeps the data.

To reset **only** Postgres, re-applying migrations and the seed from scratch:

```bash
docker compose rm -sf postgres
docker volume rm nexus_postgres_data
docker compose up -d postgres
```

The schema is applied by `docker/postgres-init.sh`, which the `postgres` image
runs automatically the first time the data volume is created. It loops over
`db/migrations/*.sql` in filename order, so a new migration added later
needs no change to the compose file or the script.

To browse tables:

```bash
docker exec -it nexus-postgres psql -U postgres -d postgres
```

## Setup

Needs an Apple-silicon Mac, Docker Desktop, [uv](https://docs.astral.sh/uv/) and
Node 20.9+ (Next 16 requires it).

```bash
cp .env.example .env      # then set NEO4J_PASSWORD, JWT_SECRET and SERVICE_TOKEN
uv sync                   # core, memory, the RAG pipeline and the MLX runtime, editable
cd frontend && npm install && cd ..
```

Then, from the repo root:

```bash
./start.sh                     # runtime (8003) + containers + memory (8000) + RAG API (8001) + core (8100)
cd frontend && npm run dev     # separate terminal
```

Open http://localhost:3000/chat. Ctrl+C stops the Python services and leaves the
containers running. To run a service by hand, see [`RUNBOOK.md`](RUNBOOK.md).

### Local inference

Every model call goes through `core/nexus_core/providers/` or graphiti's
OpenAI-compatible client, pointed by `OPENAI_BASE_URL` at the runtime
(`runtime/`), which runs every model on Apple's MLX in one process. One chat
model serves answers, the write gate, titles and graph extraction, and an
embedding model embeds for the graph; the runtime schedules them by urgency so
background extraction never delays a reply. The RAG pipeline embeds and
reranks in-process on CPU. Model names are configuration (Hugging Face ids of
MLX conversions), downloaded on first start. Ollama remains a documented
fallback. How the models fit in memory and how they are chosen: `RUNBOOK.md`.

## Repository layout

```
core/nexus_core/        auth, chats, projects, documents, routing, the write gate, voice
memory/nexus_memory/    the graph: scopes, ingest, retrieval, maintenance, live updates
rag/                    the RAG pipeline (rag_pipeline/) and its HTTP service (rag_api.py)
runtime/nexus_runtime/  every local model on MLX: scheduler, chat, embeddings, Laya, speech, residency
websearch/              the one path to the internet: redaction, checks, audit log (a container)
frontend/               Next.js 16, hand-written CSS
db/                     Postgres schema (migrations/) and seed, source of truth
docker/                 Postgres init script and SearXNG config
docs/                   design notes, benchmarks/, history/ -- kept locally, not in git
scripts/                benchmarks (text, voice), extraction check, floor sweep, graph re-embed,
                        Laya data and fine-tuning (laya/)
tests/                  end-to-end scenarios (opt-in, drives the live stack)
```

Each service owns its tests: `core/tests/`, `memory/tests/`, `rag/tests/`,
`runtime/tests/`, `websearch/tests/`.

The Python packages are named `nexus_core`, `nexus_memory` and `rag_pipeline`
rather than `app`. Two were both `app` once, installed into the same
virtualenv, which meant `import app` resolved to whichever won and the other
was unreachable.

## Tests

```bash
uv run pytest                            # every service: ~350 tests, about a minute
RUN_MLX=1 uv run pytest runtime/tests    # the runtime on real (small) MLX models
RUN_SCENARIOS=1 uv run pytest tests -v   # end-to-end against the live stack and local models
```

Database tests need the Postgres container and skip themselves without it. The
RAG suite loads real embedding and reranking models, which is most of the time.

The scenario suite is the one that answers whether memory works: a fact stated
in one chat recalled in another, a project fact staying out of other projects, a
personal fact following you between them, and chit-chat writing nothing.

## Further reading

- [`RUNBOOK.md`](RUNBOOK.md): day-to-day operation, model memory limits, and
  the failures that cost time in practice.
- [`docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`](docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md):
  open product and safety decisions.
- [`docs/RAG_API_SERVICE.md`](docs/RAG_API_SERVICE.md): why document retrieval
  is its own service.
- `docs/history/CHECKPOINT_0*.md`: records of earlier states, kept for the
  reasoning rather than the instructions.
