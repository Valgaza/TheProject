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
| SearXNG | none | Private metasearch. No host port: only the websearch container can reach it. |
| websearch | 8004 (localhost only) | `websearch/` — the one path to the internet. Redacts, checks, audits. Answers `core` only. |
| `nexus_memory` | 8000 | Owns the graph: ingest, search, live updates. Answers `core` only. |
| RAG API | 8001 | `rag/rag_api.py` — the only way `core` reaches the RAG pipeline. Answers `core` only. |
| `nexus_runtime` | 8003 | Every local model, on MLX: chat, embeddings, the decision model (Laya), speech-to-text, text-to-speech. OpenAI-compatible API. Answers `core`, `memory` and websearch only. |
| `nexus_core` | 8100 | Owns chat, the orchestrator, providers, and Postgres. |
| Frontend | 3000 | Next.js. |

Two databases, two jobs: **Postgres stores what was said**, every message,
always. **Neo4j stores what was learned**, gated. Neither is a cache of the
other. Qdrant is a third store with a narrower job: the chunks of uploaded
documents, searched by the RAG pipeline.

All five containers are declared in the root `docker-compose.yml` and start
together with one `docker compose up -d` (which `./start.sh` runs for you).

## First-time setup

```bash
cp .env.example .env          # then fill in NEO4J_PASSWORD, JWT_SECRET and SERVICE_TOKEN
uv sync                       # every service editable, the MLX runtime included
cd frontend && npm install && cd ..
```

No separate model install: the runtime downloads the models named in `.env` the
first time it starts (about 5 GB for the default chat model; at a slow
connection's ~1 MB/s, budget over an hour). To fetch them ahead of time:

```bash
uv run python -c "from huggingface_hub import snapshot_download as d; \
  d('mlx-community/Qwen3-8B-4bit'); d('mlx-community/embeddinggemma-300m-4bit')"
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
the laptop except an explicitly requested web search. Every model lives in one
process, the runtime (`runtime/`, port 8003), on Apple's MLX. It serves an
OpenAI-compatible API, so `core` and graphiti reach it with nothing more than:

```
OPENAI_BASE_URL=http://localhost:8003/v1
OPENAI_API_KEY=${SERVICE_TOKEN}        # the runtime checks it like memory and RAG do
```

`./start.sh` starts the runtime first, so its models load while the containers
boot, and waits for `curl -s localhost:8003/health` to say `"status": "ok"`
before starting anything that calls it. The first start downloads the models
from Hugging Face into `~/.cache/huggingface`; after that, loading takes
seconds. To run it by hand:

```bash
uv run uvicorn nexus_runtime.main:app --port 8003     # no --reload: it would reload the weights
```

### Why one process owns every model

There is one GPU. The runtime runs all model work on a single worker thread,
one step at a time (a step is one generated token), and before every step picks
the most urgent job waiting (`runtime/nexus_runtime/engine.py`). Callers say how
urgent they are with an `X-Nexus-Priority` header:

| Priority | Who sends it |
|---|---|
| `live` | the answer someone is waiting for; every embedding |
| `interactive` | titles, the write gate, the web-query rewrite (the default) |
| `bulk` | graphiti's extraction (`memory/nexus_memory/main.py`) |

So a reply overtakes a background extraction within one token of arriving,
and extraction resumes where it stopped. Under Ollama the two simply competed:
the baseline measured memory writes stretching to 70-120 s and timing out.

It also keeps each conversation's KV cache between turns
(`RUNTIME_PROMPT_CACHE_*`), so a follow-up only processes what is new, and it
enforces `response_format: json_schema` with xgrammar's token masks, so
graphiti's structured extraction cannot come back as malformed JSON.

`GET /models` shows what is loaded and how much memory it holds;
`POST /models/{chat|embed}/unload` and `/load` move a model in or out.

### Which model does what

One chat model covers every job, because two do not fit alongside everything
else:

| Setting | Model |
|---|---|
| `CORE_CHAT_MODEL` | `mlx-community/Qwen3-8B-4bit` (chosen by benchmark: `docs/benchmarks/model-decision.md`) |
| `CORE_GATE_MODEL` | same |
| `MEMORY_EXTRACTION_MODEL` | same |
| `MEMORY_EMBEDDING_MODEL` | `mlx-community/embeddinggemma-300m-4bit` (768-d) |

The chat model is chosen by benchmark (`scripts/bench.py` for latency,
`scripts/extraction_check.py` for graph quality, `tests/test_scenarios.py` for
behaviour); the record goes in `docs/benchmarks/`. The runtime turns Qwen3's
thinking off by default (`RUNTIME_ENABLE_THINKING=0`): thinking tokens are
latency nobody sees.

**Changing the embedding model invalidates every stored vector.** After any
change to `MEMORY_EMBEDDING_MODEL` (including the move from Ollama's
nomic-embed-text):

```bash
uv run python scripts/reembed_graph.py      # recompute every name and fact vector
uv run python scripts/floor_sweep.py        # re-measure MEMORY_SEARCH_MIN_SCORE
```

Document vectors are unaffected: the RAG pipeline embeds with its own model.

### The memory ceiling

macOS lets the GPU use about 75% of installed RAM by default: **17.8 GiB** on
this 24 GB M5 Pro. That ceiling covers the model, its KV cache and the prompt
cache together. `RUNTIME_MEMORY_LIMIT_GB` caps what MLX may take below it. The
limit itself can be raised:

```bash
sudo sysctl iogpu.wired_limit_mb=20480     # resets on reboot
```

Starving macOS of memory to feed the GPU destabilises the whole system, so
treat that as an experiment, not a default. The containers are capped in
`docker-compose.yml` (Neo4j's heap and page cache included) for the same
reason: every gigabyte they do not take is one the model can.

### Ollama, the fallback

Until the MLX runtime has proven itself through the eval, Ollama still works:
set `OPENAI_BASE_URL=http://localhost:11434/v1` and Ollama tags for the model
names (`gpt-oss:20b`, `nomic-embed-text`), and `start.sh` starts Ollama instead
of the runtime. Switching embedding models either way means re-embedding.

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
asking in words ("search the web for…", and, once the decision model is live
for it, other phrasings too). Nothing in core talks to the internet. Core
sends the message to the websearch container (`websearch/`, :8004), which:

1. has the runtime's LLM rewrite it as a generic topic, then strips your
   name, your email and anything with five or more digits from the rewrite;
2. asks the decision model whether the query still identifies someone
   (`WEBSEARCH_LAYA_QUERY`: in `live` it blocks the search above 0.8);
3. sends it to SearXNG, falling back across engines when one returns nothing;
4. appends one line to its audit log: the exact text that left, when, and for
   whom. Never the original message.

If the rewrite fails, nothing is sent. Each web answer shows the exact query.
SearXNG has no host port, so the websearch container is the only thing that
can reach it. Read the audit log:

```bash
curl -s -H "X-Service-Token: $SERVICE_TOKEN" localhost:8004/audit | jq .
docker compose exec websearch tail -n 20 /data/audit.jsonl
```

Core, memory and RAG still run on the host, so Docker does not block their
egress; they simply have no code that reaches out. See flag 1 in
`docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`.

## The decision model (Laya)

A 421M-parameter encoder in the runtime answers classification questions in
about 15 ms, where an LLM call takes seconds. Each place it is used has a
mode, `off`, `shadow` or `live`:

| Point | Setting | What it decides |
|---|---|---|
| Memory gate | `CORE_LAYA_GATE` | Worth remembering? If clearly not, the gate's LLM call is skipped |
| Web intent | `CORE_LAYA_WEB_INTENT` | Is the message asking to search the web? Can only turn the web on |
| Outgoing query | `WEBSEARCH_LAYA_QUERY` | Does the query still identify someone? Blocks it |

In `shadow` its answer is written to `decision_log` beside what the existing
logic decided, and the existing logic acts. A point goes `live` when
`scripts/laya/evaluate.py` shows it meets the bar on the hand-labelled sets in
`scripts/laya/labels/` (the decision record: `docs/benchmarks/laya-decision.md`). To see how
shadow decisions compare on real traffic:

```bash
docker compose exec postgres psql -U postgres -c \
  "select point, mode, laya, baseline, laya_ms from decision_log order by created_at desc limit 20"
```

The fine-tuned checkpoint is built locally (synthetic data from the resident
LLM; the labelled sets are held out):

```bash
uv run python scripts/laya/synth.py            # examples, with the stack up
uv run python scripts/laya/export.py           # -> data/laya/train.jsonl, val.jsonl
data/laya/.venv/bin/python scripts/laya/finetune.py   # torch on MPS, ~1 h
# then in .env: RUNTIME_DECIDE_MODEL=data/laya/finetuned, RUNTIME_DECIDE_SUBFOLDER=
uv run python scripts/laya/evaluate.py --out docs/benchmarks/laya-finetuned
```

`data/laya/.venv` is separate on purpose: fine-tuning needs torch, and nothing
the services run does.

## Voice

The mic button in a chat starts a hands-free conversation: no push-to-talk.
Speak, pause, and Nexus answers aloud; talk over it to interrupt. Every spoken
turn is an ordinary message in the chat (`meta.voice` holds its timings), with
the same memory, documents and web as typed ones. An interrupted answer is
stored as only what you heard.

How it is wired (`core/nexus_core/voice/pipeline.py`, on Pipecat):

- The browser's Pipecat client (`frontend/src/lib/voice.ts`) posts a WebRTC
  offer to core (`POST /voice/{chat}/offer`, session cookie and Origin
  checked); microphone and answer audio then travel over the peer
  connection, with the browser's own echo cancellation. Transcripts, the
  streamed answer and the stored messages arrive as RTVI messages on its data
  channel.
- Core runs voice activity detection (Silero) and **Smart Turn v3**, a small
  model that listens to *how* something was said to tell a finished sentence
  from a pause mid-thought. A transcript that trails off ("tell me about
  the…") is held for more, and speaking again within two seconds of a turn
  ending continues that turn.
- Speech-to-text (Parakeet) and text-to-speech (Kokoro) run in the runtime at
  live priority. A long first sentence is spoken from its first comma, and
  each piece is synthesised while the previous one plays.
- Speech over the answer stops it on three or more words, a stop phrase
  ("stop", "wait", "hold on"), or a second of continuous speech. "Mm-hmm"
  never does, and speech that ends while the answer is still going without
  stopping it is not answered.
- The memory write queue waits for the whole session.

First time: `uv run python scripts/fetch_voice_assets.py` (speech models and
the decision model; VAD and Smart Turn come with Pipecat). Opening a session
loads speech-to-text and text-to-speech (a few seconds); they unload after
five idle minutes.

Tuning, in `.env` (restart core):

| Setting | Default | Raise it if |
|---|---|---|
| `CORE_VOICE_INCOMPLETE_WAIT_MS` | 2000 | it answers before you've finished a thought |
| `CORE_VOICE_CONTINUE_WITHIN_MS` | 2000 | a long pause splits one question into two |
| `CORE_VOICE_INTERRUPT_MIN_WORDS` | 3 | it stops on short remarks it should talk through |
| `CORE_VOICE_INTERRUPT_AFTER_MS` | 1000 | background talk stops it |
| `CORE_VOICE_VAD_STOP_MS` | 200 | (rarely) noise ends turns early |

Troubleshooting:

- **Nothing happens when I speak.** The browser must be allowed the mic
  (address-bar permission), and the page must be on `localhost` or https.
- **It interrupts itself.** Its own voice is coming back through the mic.
  Use headphones; echo cancellation helps but speakers right next to the mic
  can still leak.
- **The mic button is greyed out.** It only works inside a chat, and not
  while an answer is being written.
- **Unknown words are spelled out letter by letter.** The espeak-ng that
  Kokoro's phonemizer ships with is broken on macOS, so the runtime falls back
  to spelling (`runtime/nexus_runtime/pronunciation.py`). `brew install
  espeak-ng` and restart the runtime for real pronunciation.
- **Measure it:** `uv run python scripts/voice_bench.py --exchanges 20 --out
  docs/benchmarks/<date>-voice` drives the WebRTC endpoint like a browser and
  reports end-of-speech to first audio, transcription accuracy, interruption
  time, the hesitant / "mm-hmm" / spoken-web checks, and memory.

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

RUN_MLX=1 uv run pytest runtime/tests
                               # the runtime on real small MLX models
```

The scenario suite is the one that answers whether the product works. Memory:
cross-chat recall, cross-project isolation, personal facts following you between
projects, the write gate keeping chit-chat out of the graph. And the eval
(`tests/test_eval_scenarios.py`): a document question cited to the right file, a
web search whose audit log holds no identifying detail, and a spoken question
sent over WebRTC as a microphone would and answered aloud. Each signs up a throwaway
user and removes it afterwards.

Benchmarks, with the stack up. Reports go to `docs/benchmarks/`, which stays on
the machine that ran them: `docs/` is not in git.

```bash
uv run python scripts/bench.py --out docs/benchmarks/<date>-text           # typed turns
uv run python scripts/voice_bench.py --out docs/benchmarks/<date>-voice    # spoken turns
uv run python scripts/extraction_check.py --out docs/benchmarks/<date>-extraction
scripts/compare_models.sh <model id> ...    # all of the above per candidate chat model
```

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
silently, as above. The runtime turns thinking off and strips any
`<think>…</think>` span from what it returns, so this only bites on Ollama.

**A model is "too large" but the machine has 24 GB.** macOS caps
GPU-wired memory at roughly 75% of RAM — 17.8 GiB here — and that cap covers
the KV cache as well as the weights. Ollama prints its real ceiling at startup
(`inference compute ... total="17.8 GiB"`); for the runtime, `GET /models`
shows active and peak MLX memory.

**Downloads hang at 0 bytes.** The Hugging Face client fetches through its Xet
storage backend, which can stall on some networks. `HF_HUB_DISABLE_XET=1`
falls back to plain HTTPS.
