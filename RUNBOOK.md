# Nexus — running it locally

Current as of 2026-10-04 (repo layout: `rag/`, `db/`, `docs/`; `orchestrator/` removed). `docs/history/CHECKPOINT_0*.md` are historical
records of earlier states and describe a layout that no longer exists; this is
the file to trust.

## What runs where

| Piece | Port | What it is |
|---|---|---|
| Postgres | 54322 | Chats, messages, projects. One Docker container. |
| Neo4j | 7474 / 7687 | The knowledge graph. One Docker container. |
| Qdrant | 6333 / 6334 | Document vectors for RAG. One Docker container. |
| SearXNG | 8080 | Private metasearch. `core`'s web handler is its only caller. |
| `nexus_memory` | 8000 | Owns the graph: ingest, search, live updates. Answers `core` only. |
| RAG API | 8001 | `rag/rag_api.py` — the only way `core` reaches the RAG pipeline. Answers `core` only. |
| Ollama | 11434 | Local inference. Serves both chat and embeddings. |
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
cp .env.example .env          # then fill in NEO4J_PASSWORD, JWT_SECRET and SERVICE_TOKEN
uv sync                       # installs both services editable
cd frontend && npm install && cd ..

brew install ollama           # pulls mlx as a dependency — the fast path here
ollama pull gpt-oss:20b       # 13 GB. chat + write gate + graph extraction
ollama pull nomic-embed-text  # 274 MB. embeddings only
```

`JWT_SECRET` has no default and the core service refuses to start without it —
an empty signing secret means anyone can forge a session for any user, so it
fails loudly rather than running insecure. Generate one with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
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

Postgres is just another container in `docker-compose.yml` (it replaced the
Supabase CLI on 2026-09-02), and the schema in `db/migrations/` is applied
automatically the first time its volume is created. Do **not** run
`supabase start`: it would try to bind port 54322 and collide with the
Postgres container.

To run a service by hand instead:

```bash
uv run uvicorn nexus_memory.main:app --reload --port 8000
uv run uvicorn rag_api:app --app-dir rag --reload --port 8001
uv run uvicorn nexus_core.main:app   --reload --port 8100
```

**From the repo root, and through `uv run` or `.venv/bin/uvicorn`.** No
`PYTHONPATH`, no `cd core`, and not the system `uvicorn` — that is a different
interpreter with none of this installed.

The old command `uvicorn app.main:app` no longer works. Both services used to
ship a top-level package called `app`, so whichever was imported first won and
the other was unreachable. They are `nexus_memory` and `nexus_core` now.

## Running the models locally

Everything runs on this machine. No API key, no credit, no network call leaves
the laptop. Both services reach one Ollama daemon through its OpenAI-compatible
endpoint:

```
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama-local-no-key-needed   # required by config, ignored by Ollama
```

`OPENAI_API_KEY` is still `_require`d by both configs, so it has to hold
something — but Ollama never reads it. It is a placeholder, not a credential.

Start the daemon:

```bash
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 \
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

All three matter on a 24 GB machine. The q8 KV cache roughly halves what the
context window costs, which is what makes the third flag affordable: Ollama
derives a default context from available VRAM and lands on **4096**, far too
small for a turn that carries chat history, recalled facts and an assembled
RAG prompt together. It would truncate silently. Measured here, 16K costs
nothing extra in resident size — still 12 GB, still 100% GPU.

### Which model does what

One model covers three jobs, because loading two at once does not fit:

| Setting | Model |
|---|---|
| `CORE_CHAT_MODEL` | `gpt-oss:20b` |
| `CORE_GATE_MODEL` | `gpt-oss:20b` |
| `MEMORY_EXTRACTION_MODEL` | `gpt-oss:20b` |
| `MEMORY_EMBEDDING_MODEL` | `nomic-embed-text` |

`gpt-oss:20b` is a 21B mixture-of-experts model with roughly 3.6B parameters
active per token, so it reads a fraction of its weights on each step and runs
far quicker than 21B suggests. Its MXFP4 weights are **native** — OpenAI
trained it in 4-bit rather than quantising a larger model afterwards — which
matters here more than raw benchmark scores. Post-hoc 4-bit quantisation costs
1.5–3% perplexity, and that damage lands disproportionately on precise
reasoning and structured output: exactly what the write gate and graphiti
extraction do. A natively-4-bit model never paid that tax.

### The memory ceiling

Ollama reports what it can actually use at startup:

```
inference compute ... name=MTL0 description="Apple M5 Pro" total="17.8 GiB"
```

That 17.8 GiB is macOS's default GPU wired limit, about 75% of installed RAM.
It caps the model **plus** its KV cache, not just the weights. `gpt-oss:20b` at
13 GB leaves roughly 4 GB of headroom; an 18 GB model does not fit at all, even
though the machine has 24 GB. The limit can be raised:

```bash
sudo sysctl iogpu.wired_limit_mb=20480     # resets on reboot
```

Starving macOS of memory to feed the GPU destabilises the whole system, so
treat that as an experiment, not a default.

### Graphiti needs the generic client

`memory/nexus_memory/main.py` builds `OpenAIGenericClient`, **not**
`OpenAIClient`. This is not interchangeable. The generic client is what
graphiti documents for any OpenAI-compatible server that is not OpenAI itself:
it defaults to a 16K token ceiling instead of 8K, and it handles structured
output the way local runtimes actually implement it. `OpenAIClient` assumes
OpenAI's own extensions and mis-parses what a local model returns.

Both the LLM client and the embedder now take `base_url` from
`OPENAI_BASE_URL`. Before that they were hardwired to `api.openai.com` and
ignored the setting entirely, which meant `core` could be pointed anywhere
while `memory` silently kept calling OpenAI.

## Checking it works

```bash
curl -s localhost:8000/health   # {"status":"ok","neo4j":"ok"}
curl -s localhost:8100/health   # provider, chat_model, memory_service, database
```

`core`'s health reports `memory_service` and `database` separately, and always
returns 200 — read the fields, not the status code.

Then open http://localhost:3000/chat and say something. To watch rows land:
`docker compose exec postgres psql -U postgres -c 'select role, left(content, 60) from messages order by created_at desc limit 5'`.

## Who can call what

The browser talks to `core` and nothing else. `core` checks the session
cookie, then calls memory and RAG with `SERVICE_TOKEN` in an `X-Service-Token`
header. Memory and RAG refuse every request without it except their health
checks, so knowing someone's user id no longer gets you their graph or files.
The graph panel's reads, resets and live socket all go through `core`
(`/graph`, `/ws/graph`).

Set `SERVICE_TOKEN` once in the root `.env`. All three services read the same
file. If it's missing, `core` and memory refuse to start, and RAG logs an
error and refuses everything.

## Web search

Off unless asked for, per message: the globe button in the composer, or
typing "search the web" / "search the internet". The query that goes to
SearXNG is never what you typed. A model rewrites it as a generic topic, then
`core/nexus_core/web_client.py` strips your name, your email, and anything with
five or more digits. If the rewrite fails, nothing is sent. Each web answer
shows the exact query that left the machine. See flag 1 in
`docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`.

## Documents and their tiers

A document belongs to exactly one tier, decided by where it was uploaded:

| Uploaded | Tier | Retrievable from |
|---|---|---|
| `/documents` | universal | every chat, in every project |
| a project page | that project | that project's chats only |

The rule is `rag/rag_pipeline/scopes.py`, and it deliberately mirrors the
graph's `memory/nexus_memory/scopes.py`. A project question reads its own
documents *and* the universal ones; a loose question reads the universal ones
alone. Two projects never see each other. If you change one of those files,
change the other -- otherwise a fact and the document it came from stop being
visible in the same places.

Chunks carry their tier in the Qdrant payload as `scope_key`, and a read
passes the list of keys it may see. Qdrant's MatchAny turns that list into an
OR, so `read_scopes()` is the entire access rule.

### Documents are a retrieval tier, not a route

`select_route()` no longer asks whether documents exist. Every turn retrieves
against the scoped corpus and a relevance floor (`CORE_RAG_MIN_SCORE`,
default 0.5) decides whether anything reaches the model -- exactly how memory
has always worked with its cosine floor.

The old design routed the whole turn on "does this project have a corpus",
which meant one uploaded PDF sent every message in that project through
retrieval, "thanks" included. A floor answers the question per turn instead.

The `route` recorded on a message says `rag` only when documents actually
cleared the floor, so the label describes what happened rather than what was
predicted.

### What the graph learns from a document

On upload, `core/nexus_core/document_gate.py` reads the document's opening
and writes at most three facts to the graph, at the document's own tier.

They are deliberately facts about *holdings*, not about content:

```
good  Aarya has a reference document, raft.md, on the Raft consensus algorithm.
bad   Raft elects a leader using randomised timeouts.
```

The second belongs in the retrieval index. Feeding document text to graphiti
would extract hundreds of entities and build a worse copy of the corpus in a
store designed for a different job. The graph answers "does this person have
anything on consensus?"; retrieval answers everything else.

## Tests

```bash
uv run pytest                  # ~320 tests, about a minute (the RAG suite
                               # loads real models). Only Postgres needed;
                               # database-backed ones skip if it is down
                               # (docker compose up -d postgres)

RUN_SCENARIOS=1 uv run pytest tests -v
                               # end-to-end scenarios: needs all services up
                               # and the local model, takes several minutes
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

**After pulling the 2026-10-04 cleanup, recreate two containers and one
install.** `supabase/` became `db/` and SearXNG's config moved from
`orchestrator/searxng/` to `docker/searxng/`. A container keeps the bind
mount it was created with, and `restart: unless-stopped` brings it back on
the old path, where Docker quietly recreates the directory (SearXNG even
writes a default config there, with JSON output off, so web search returns
nothing). Data volumes are untouched by this:

```bash
docker compose up -d postgres searxng          # recreated on the new mounts
uv sync --reinstall-package rag-pipeline        # editable install now at rag/
```


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

**Changing `EMBEDDING_DIM` invalidates every stored vector.** Similarity is
only meaningful between vectors of the same length. It moved 1536 → 768 on
2026-09-03 when embeddings went local (`nomic-embed-text` is 768), and the
graph had to be wiped and rebuilt. If you change it again, wipe again:

```bash
docker exec nexus-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (n) DETACH DELETE n;"
```

**A background task does nothing, silently.** FastAPI runs background tasks
*before* the exit code of a dependency that yields, so `session_dependency`'s
commit lands **after** the task. A task that opens its own session then finds
no row for something the request just created, and returns. Commit explicitly
before scheduling -- both `chats.py` and `documents.py` do, with a comment
saying why. Symptom: an upload succeeds, `graph_synced_at` stays null forever,
and nothing is logged anywhere.

**Memory silently stops recording anything.** The write gate parses the model's
JSON at `core/nexus_core/write_gate.py:214` and, on unparseable output, returns
`GateDecision.skip(...)` — it fails *soft*. Chat keeps working perfectly while
nothing is ever written to the graph, and nothing errors. The usual cause is a
model emitting reasoning before its JSON, or wrapping it in prose. Check the
core log for `gate returned unparseable output` before blaming the graph.

**A local model that reasons out loud breaks the write gate.** `_parse`
tolerates a ```json fence and nothing else. Any model whose chain-of-thought
lands in the response body rather than a separate field fails every gate call,
silently, as above.

**Ollama says a model is too large but the machine has 24 GB.** macOS caps
GPU-wired memory at roughly 75% of RAM — 17.8 GiB here — and that cap covers
the KV cache as well as the weights. Ollama prints its real ceiling at startup:
`inference compute ... total="17.8 GiB"`.
