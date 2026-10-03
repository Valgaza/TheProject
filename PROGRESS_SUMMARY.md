# NexusAI — Progress Summary

Personal record of what's been built and why, for Bhoumish's own reference.
Originally kept out of git on purpose (see `.gitignore`); committed on
2026-10-03 at Bhoumish's request, so it is now tracked despite that entry.
For that, see `.github/PROJECT_STATUS_REPORT.md` and
`.github/IMPORTANT_BUSINESS_LOGIC_FLAGS.md`. For the live backlog, see
`steps-to-implement-Bhoumish.md`.

Last updated: 2026-10-03 (latest code changes dated 2026-09-04).

## What NexusAI is

A locally-run, multi-pipeline AI orchestration system, pitched at enterprises
(law firms, hospitals) who can't send client data to the cloud. One chat
window; an orchestrator invisibly routes each message to the right pipeline
(RAG document Q&A, conversational, and — not yet built — vision and
speech-to-text), folds in a knowledge graph of durable facts, and — only
when explicitly asked — live results from a self-hosted web search. Deployed
per-customer, one instance each, on-premise or on a single cloud box (AWS
EC2 planned for the demo day).

Team: four people, one pipeline each. Bhoumish owns RAG. This summary covers
the `feature/orchestrator` branch — `core` (the orchestrator/chat service)
and the infrastructure around it, built collaboratively with Claude.

## Architecture, as it stands

- **`core`** (FastAPI, port 8100) — the orchestrator. Owns chat, projects,
  auth, routing, and prompt assembly. The only service the frontend and any
  outside caller should ever talk to (memory is a partial exception — see
  below).
- **`memory`** (FastAPI, port 8000) — the knowledge graph, via Neo4j +
  Graphiti. A teammate's service; `core` calls it, and so does the frontend
  directly for the live graph view.
- **RAG API** (FastAPI, port 8001, `RAG/rag_api.py`) — document retrieval,
  backed by Qdrant. Bhoumish's own service; `core` calls it over HTTP,
  they're separate OS processes so there's no other way in. Was called
  `controller/` until renamed 2026-09-02 for clarity. Every chunk is tagged
  with its owner (`user_id`/`project_id`) and every read is scoped to it
  (2026-09-04, see §15).
- **Frontend** (Next.js, port 3000) — the chat UI.
- **Infrastructure**: Postgres (chat transcripts), Neo4j (the graph), Qdrant
  (document vectors), SearXNG (private web search, wired into `core` as of
  2026-09-03) — four Docker containers, one `docker-compose.yml` for all of
  them. The old standalone `orchestrator/` scaffold no longer exists; its
  useful pieces (web client, retry logic) live in `core` now.

## Major work completed

### 1. RAG wired into the answering flow
`core` didn't call RAG at all until this pass. Added `core/nexus_core/rag_client.py`,
made `select_route()` async so it can check `rag.has_documents()` and route
accordingly, and folded retrieved passages into the prompt the same way
recalled memory facts already were. `TurnOut.rag_sources` exposes what was
used, same shape as the existing `recalled` field.

### 2. `uv` workspace bug (dependency-deleting)
`RAG/` wasn't declared as a workspace member, so a plain `uv sync` silently
deleted its ML dependencies (torch, transformers, qdrant-client...) every
time. Fixed by adding `RAG` to `[tool.uv.workspace] members` and
`rag-pipeline` to the root dependencies. Verified: `uv sync` no longer
touches RAG's deps, full test suite still passes.

### 3. `controller/` renamed to `RAG/rag_api.py`
The name was actively confusing — "controller" sounded like the central
brain, which is `core`'s job. Moved into RAG's own folder, renamed, deps
merged into `RAG/pyproject.toml` (no more separate `requirements.txt`).
Every reference across the repo updated (README, `.env.example`,
`rag_client.py`, docs).

### 4. Supabase CLI replaced with a plain Postgres container
The Supabase CLI's local stack started twelve containers (Auth, Storage,
Realtime, Studio, an email catcher...) for the one Postgres this project
actually uses. Replaced with a lean `postgres:17` container, merged into one
root `docker-compose.yml` alongside Neo4j, Qdrant, and SearXNG (the latter
two moved in from their own now-deleted compose files). `docker/postgres-init.sh`
applies every file in `supabase/migrations/` plus `seed.sql` automatically
the first time the container's volume is created — `supabase/` stays the
schema source of truth, only the tool that applies it changed. `start.sh`
rewritten to start all four containers, wait for Neo4j *and* Postgres
specifically (not just "the container is up"), then launch all three Python
services.

**Bugs found and fixed along the way:**
- Test pollution: `app.state` is a module-level global shared across the
  whole pytest session, so a client left on it by one test leaked into the
  next. Fixed with an explicit reset fixture.
- `core/.env` silently shadowing the root `.env` — bare `load_dotenv()`
  searches upward and stops at the first hit. A stray `core/.env` with an
  *empty* key beat the real root `.env` two directories up. Fixed by loading
  the root path explicitly. (`memory/nexus_memory/config.py` still has this
  latent bug — not fixed, it's a teammate's file, flagged in the backlog.)

### 5. Real authentication (email/password + Google OAuth)
Previously every request in `core` acted as one hardcoded demo user — no
real login existed anywhere. Built from scratch: bcrypt password hashing,
JWT session tokens in an httpOnly cookie (`core/nexus_core/auth.py`),
signup/login/logout/me endpoints, and Google OAuth via Authlib (not
Passport.js — that's Node-only). Self-service signup, deliberately, for the
demo/evaluation — invite-only is a real future decision, not built.
`profiles` gained `email`/`password_hash`/`google_id` via migration.
Existing routes (`chats.py`, `projects.py`) switched from
`settings.demo_user_id` to the real session-derived user id — one-line
change each, exactly as their own old docstrings predicted. 9 new tests;
zero changes to any pre-existing test body (the `db_client` fixture now logs
in as the demo user via a crafted JWT instead of the old hardcoded id).

**Bugs found by live testing, not just pytest:**
- `demo@nexus.local` was rejected by `EmailStr` — `.local` is an RFC
  special-use TLD, syntax-rejected regardless of network access. Confirmed
  via source inspection that this doesn't imply a live-internet dependency
  for signup (pydantic hardcodes `check_deliverability=False`). Fixed by
  switching the demo domain to `demo@example.com`.

### 6. Frontend wired to real auth
Login/signup page (tabbed, one component), `credentials: "include"` added to
every `core` fetch so the session cookie actually travels, hardcoded
`USER_ID` constant removed from `frontend/src/lib/api.ts` in favor of a
module-level id set once the session resolves. A `RequireAuth` gate in
`app-shell.tsx`: `/` and `/login` are the only public routes, everything
else redirects to `/login` until a session is confirmed. Sign-out control
added to the sidebar (there wasn't one anywhere before). `/demo` and
`/rag-demo` (stale pre-`core` showcase pages) deleted; the landing page's
CTA now points at the real `/chat`.

**A real bug found live:** the project list never actually refetched after
login — `ProjectProvider` fired its one fetch on mount, before the user was
authenticated, and nothing ever asked again. Fixed by keying the fetch off
the resolved auth state instead of firing once.

**Flagged, not fixed:** the memory service has no session-cookie auth of its
own — it trusts whatever `user_id` is passed to it as a plain query
parameter. Using the real logged-in id (instead of the old shared demo
constant) makes each account's graph practically separate, but doesn't
close the gap: anyone who knew another user's id could still pass it
directly. A prompt was drafted for the teammate who owns `memory/` to pick
their own fix for this — deliberately left open-ended ("what", not "how").

### 7. UI polish pass
- Login page was rendering squashed into a narrow leftover sidebar column
  (the CSS grid always reserved a rail-width track; only the landing page
  was told to collapse it). Fixed by extending the same full-bleed
  treatment to `/login`.
- A floating "Tweaks" design-preview panel (accent color, font pairing,
  landing page background style, motion intensity) was mounted globally,
  forced open on *every* page, and reset to defaults on every reload
  (never persisted). Moved its controls into Settings → Appearance, and
  made the underlying state persist to `localStorage`.
- API error messages were showing raw JSON (`{"detail":"..."}`) straight to
  the user. Fixed by parsing FastAPI's error envelope in the shared fetch
  helper.
- Login's generic "incorrect email or password" (deliberately identical for
  wrong-password and unknown-email, to prevent account enumeration) was kept
  as-is on purpose after discussing the tradeoff — added a "New here? Create
  an account" nudge that shows for *every* failure alike instead, so it adds
  friendliness without leaking which case actually happened.

### 8. Chat titles, generated the way ChatGPT/Claude do it
Originally chats were either left as "New conversation" or named from a
truncated first line of the prompt. Replaced with a real model call
(`core/nexus_core/chat_title.py`) that writes a short, topical title —
mirrors the existing "write gate" pattern (its own cheap model call, never
raises, falls back safely). Runs *concurrently* with the answer generation
via `asyncio.create_task`, so a good title costs nothing in wall-clock time.
The sidebar ("Recent") only updates once the full turn finishes — tried
showing it instantly on send first, but that was explicitly reverted: shows
up after the response, by design.

**A real bug found live:** Gemini 3.x (see below) is a "thinking" model —
it spends part of its token budget on invisible reasoning before writing
anything visible. The original `max_tokens=20` for the title call was
entirely consumed by that reasoning, silently producing empty output every
time (correctly triggering the fallback, but never actually using the smart
title). Raised to `max_tokens=400` after confirming live that 20 always
failed, 150 was inconsistent (sometimes cut the title off mid-word), and
400 was reliably clean across several real test messages.

### 9. The `OPENAI_BASE_URL` bug — found, root-caused, fixed everywhere
`core`'s chat endpoint and `memory`'s `/search` endpoint both started
failing with `openai.APIConnectionError: Connection error`, no further
detail. Root cause, confirmed by direct reproduction outside the app
entirely: `.env` had `OPENAI_BASE_URL=` (intentionally blank, meaning "use
the default"). `load_dotenv()` sets a present-but-blank line to an *actual*
empty string in the process environment — not "unset". Both the openai
SDK's own internal fallback (`if base_url is None: os.environ.get(...)`)
and graphiti_core's `OpenAIEmbedder` treat "present but empty" as a real
override rather than "unset", so every request silently went to no host at
all (`httpx.UnsupportedProtocol`, deep inside a retry loop, surfacing as a
generic connection error with zero mention of `base_url`). Fixed at the
root: `.env`/`.env.example` now comment the line out entirely rather than
leaving it blank, which closes this for every current and future service —
not just a patch in `core`'s own provider code (which also got a defensive
fix, resolving the default itself rather than trusting the SDK's env
re-read).

### 10. Switched to Gemini for testing (OpenAI account has no credits)
`core`'s `OpenAIProvider` already supported pointing at any
OpenAI-compatible server (that knob existed for a future local-model swap).
Gemini has an official OpenAI-compatible endpoint, so this was a config-only
change, zero code: `OPENAI_API_KEY` → a free Gemini key,
`OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`,
`CORE_CHAT_MODEL=gemini-3.6-flash` (confirmed live against the actual key —
the 2.5 line is retired for new keys, Google's own error message points new
callers at 3.6). Verified end-to-end in the browser with real, full-quality
answers and real smart titles.

**Update, same day:** memory now runs on Gemini too, end to end — search,
extraction, and storage. Two things were needed, not one:

- `memory/nexus_memory/config.py` gained the same `openai_base_url` reading
  as `core`, threaded into `main.py`'s `LLMConfig`/`OpenAIEmbedderConfig`
  (both already accepted a `base_url` param — confirmed by reading the
  installed package — `memory`'s code just never passed one).
- That alone wasn't enough: graphiti's default `OpenAIClient` does
  structured extraction through OpenAI's newer **Responses API**
  (`client.responses.parse`), which real OpenAI serves but which Gemini's
  OpenAI-compat endpoint returns a bare 404 for (confirmed live —
  embeddings and plain chat completions both worked fine through Gemini,
  only this specific API surface didn't). Swapped to graphiti's own
  `OpenAIGenericClient`, built for exactly this case — plain Chat
  Completions with a `json_schema` response format instead, which Gemini's
  endpoint does support (also confirmed live, including the actual
  extraction: ingested "Bhoumish works at Godrej Capital as a software
  engineer trainee," then retrieved it back via a natural-language search).

`MEMORY_EXTRACTION_MODEL`/`MEMORY_EMBEDDING_MODEL` in `.env` updated to
`gemini-3.6-flash`/`gemini-embedding-001` to match. `EMBEDDING_DIM=1536`
stayed as-is — Gemini's embedding model is Matryoshka-trained (confirmed
live: requesting `dimensions=1536` directly returns a real, distinct
1536-dim vector), so graphiti's existing client-side truncation from
Gemini's native 3072 dims down to 1536 is a valid embedding, not a broken
one. 48 memory tests still pass; this was a teammate's file, touched with
their go-ahead in this session.

(The model names above are from that day. Chat, gate and extraction have
since been split onto different Gemini models to stay inside free-tier
quotas — see §13 for what's actually configured now.)

### 11. Chat and project management, composer upgrades (2026-09-03)
- **Chats**: hovering a sidebar chat shows a three-dot menu — rename
  (inline), pin/unpin, move to project (submenu), delete (with confirm).
  `PATCH`/`DELETE /chats/{id}`.
- **Projects**: same treatment on the project cards — rename, pin, delete.
  `PATCH`/`DELETE /projects/{id}`; the project context gained `update` and
  `remove` (deleting the selected project falls back to another one).
- The sidebar's "Projects" badge was a hardcoded `1`; it's now the real count.
- Migrations `20260903000000_add_chat_pinned.sql` and
  `20260903000001_add_project_pinned.sql`.
- **Composer**: Enter sends, Shift+Enter inserts a newline. The input is
  docked at the bottom of the viewport (a flex column with its own scroll
  region — `position: sticky` didn't work because an ancestor had
  `overflow: clip`, which isn't a real scroll container). The paperclip now
  works: pick `.pdf`/`.md`/`.markdown`/`.mdx` files, each gets an
  uploading/done/error chip, and they're ingested straight into RAG.

**Bugs found and fixed:**
- Deleting a chat or project raised `NotNullViolationError`: SQLAlchemy's
  default is to null out child foreign keys before deleting the parent, and
  those columns are NOT NULL. The database already had `ON DELETE CASCADE`;
  the ORM just needed `passive_deletes=True` on `Chat.messages` and
  `Project.chats` to stand aside and let it work.
- The new chat menu AND the existing project-switcher dropdown rendered
  fully transparent. Both used `background: var(--paper)` — a CSS variable
  that doesn't exist (the real token is `--bg-paper`), and an undefined
  `var()` invalidates the whole declaration. The switcher was a pre-existing
  bug; I'd copied the same mistake into the new menu before catching it.
  Fixed both.
- A "second prompt failed" report was Gemini's daily quota, not a code bug
  (see §13).

### 12. Chats no longer need a project (2026-09-03)
A chat started from `/chat` now belongs to nothing but its owner. A chat is
only filed under a project if it's opened from the Projects page
(`/chat?project=<id>`) or moved later via the chat menu. The sidebar's
project switcher is purely a view filter for "Recent" now, which merges the
selected project's chats with unfiled ones, newest first. The auto-created
"General" project is gone (first replaced with a "name your first project"
prompt, then removed entirely once chats stopped needing one).

- Migration `20260903000002_chats_optional_project.sql`: `chats.user_id`
  added (backfilled from `projects.user_id`, then NOT NULL), `project_id`
  made nullable, FK re-added with the same `ON DELETE CASCADE`.
- Ownership was previously *inferred* by joining chat → project → user. With
  a nullable `project_id` that join would have silently 404'd every unfiled
  chat for its own owner, so `_get_chat` now checks `Chat.user_id` directly.
  Caught during design, before it could ship as a live bug.
- `projects.create_chat` also had to set `user_id` (32 test failures with
  `NotNullViolationError` surfaced that).
- The memory write gate can decide a fact is "project" scope without
  knowing whether a project exists; for an unfiled chat that's downgraded to
  "user" scope in `memory_writer.py` instead of writing a project-scoped fact
  with nowhere to live. Dedicated test added.

### 13. Which model does what, and living inside free-tier quotas (2026-09-03)
Gemini's free tier has per-model daily quotas; `gemini-3.6-flash` turned out
to be ~20 requests/day, which a normal testing session burns through. The
fix was to stop using one model for everything:

| Job | Setting | Model |
|---|---|---|
| Answering the user | `CORE_CHAT_MODEL` | `gemini-flash-lite-latest` |
| Memory write gate + chat titles | `CORE_GATE_MODEL` | `gemini-3.1-flash-lite` |
| Graph fact extraction | `MEMORY_EXTRACTION_MODEL` | `gemini-3.1-flash-lite` |
| Embeddings | `MEMORY_EMBEDDING_MODEL` | `gemini-embedding-001` |

Why separate: the background jobs fire on every turn (gate) or most turns
(extraction) and don't need to be smart, so they shouldn't compete with the
model real answers depend on. Gemma and `gemini-3.6-flash` were tried for
the small jobs and both misbehaved — they "thought out loud" or spent their
whole token budget on invisible reasoning, which broke JSON parsing.
`gemini-3.1-flash-lite` answers cleanly with a small budget. This is also
the shape of the eventual local setup: a bigger model for answers, a small
local one for the gate and extraction.

Note for the architecture story: routing is still 100% rule-based
(`select_route()` and `wants_web_search()` — no model call). Hybrid
rule + LLM routing is a decided future direction, not something built.

### 14. Web search ported into `core`; `orchestrator/` retired (2026-09-03)
The standalone `orchestrator/` scaffold (port 8002, stub LLM client, called
by nothing) is deleted. What was worth keeping moved into `core`:
- `core/nexus_core/web_client.py` — SearXNG client, same shape as
  `RagClient`: never raises, 15s timeout, up to 5 results, a dead SearXNG
  degrades the answer rather than failing the turn. (`retry.py` had already
  been ported earlier.)
- `wants_web_search()` in `orchestrator.py` — a regex for "search the
  internet" / "search the web" / "look it|this up online". Deliberately NOT a
  routing `Handler`: handlers are mutually exclusive (general vs rag vs
  future vision/speech), but web search is an add-on that can run *alongside*
  RAG in the same turn, the way memory search already does. It's also the one
  piece that reaches the open internet, so it only ever fires on an explicit
  ask, never on an existence check.
- Prompt order is now: system prompt → memory → RAG → web → conversation
  (broad to narrow, with the live turn last). `TurnOut.web_sources` returns
  the cited results; `/health` reports `web_service`; config is `SEARXNG_URL`.
- SearXNG's own config moved to `docker/searxng/settings.yml` (JSON output
  enabled, which SearXNG disables by default) and `docker-compose.yml`
  mounts it from there. README and `IMPORTANT_BUSINESS_LOGIC_FLAGS.md` no
  longer point at the deleted folder.
- 12 new tests (`core/tests/test_web_context.py`). Verified live against a
  real SearXNG: genuine current results, cited in the model's answer, and an
  ordinary message correctly returns `web_sources: []`.

**Still open (a P0-ish product flag, not a code bug):** the user's raw
message is what gets sent to SearXNG and on to external search engines —
nothing redacts a patient or client name first. Decided that redaction
belongs inside `web_client.py` (the one place that talks to the internet) plus
an LLM-side instruction as defence in depth; the mechanism isn't chosen. See
`.github/IMPORTANT_BUSINESS_LOGIC_FLAGS.md` #1.

### 15. Per-user and per-project RAG scoping (2026-09-04)
**The bug, found live during demo prep:** asking about a Linux PDF in one
chat got answered with content from an unrelated GovDocHub PPT, because RAG's
Qdrant collection had no tenant concept at all — one shared pool for every
upload by anyone. Worse, `has_documents()` was also a global
`points_count > 0` check, so a single upload *anywhere* made `select_route()`
send **every chat for every user** down the RAG route. That also quietly
defeated the memory demo (RAG answered before memory could show anything).

**The fix** (built directly on this branch — `feature/rag-pipeline` turned
out to be a stale branch already fully merged, zero commits ahead):
- `ChunkMetadata` gained `user_id` and `project_id`; `IngestionPipeline.ingest()`
  stamps them on every chunk right after chunking (the chunkers stay
  ignorant of tenancy). `""` — not `None` — is the sentinel for "no owner" /
  "no project", so every filter is a plain exact match with no null handling.
  `ingest()`'s new params default to `""` so the CLI and RAG's own tests keep
  working.
- `QdrantStore.upsert()` writes both into the payload; `count()` takes an
  optional filter. The filter-building machinery (`_build_filter`) already
  existed and needed no changes.
- `rag_api.py`: `/api/ingest`, `/api/query` and `/api/status` all require
  `user_id` and accept an optional `project_id`. A project-scoped read
  filters on `project_id` alone (a project belongs to exactly one user); a
  personal read filters on `user_id` plus `project_id == ""`.
  **Deliberate simplification:** unlike memory, a project chat does NOT also
  see the user's personal unfiled uploads — scope is exact, not a union.
- `core`: `rag_client.py` takes `user_id`/`project_id` (same convention as
  `memory_client.py`), threaded through `select_route()` and
  `generate_turn()` from values already in scope there.
- Frontend: `Composer` has a `projectId` prop — the chat's own project, not
  the sidebar's selected one (they can differ); `uploadDocument(file, projectId?)`
  sends `user_id`/`project_id` as form fields.
- Tests: new `RAG/tests/test_scoping.py` (same file ingested under three
  owners; cross-owner queries never see another owner's chunks), plus the
  existing fakes/call sites updated. Also verified live over real HTTP: two
  users, two unrelated documents, a question about user A's topic asked as
  user B returned only B's own document.
- Anything uploaded before this change has no owner tag and matches no scope —
  it has to be re-uploaded.

**Same trust caveat as memory:** the RAG API trusts whichever `user_id` the
caller passes. Fine for the demo, not a security boundary.

### 16. Limitations found while preparing the demo (2026-09-04)
Not fixed — recorded so they're not rediscovered:
- **"List everything" questions fail by design.** `rerank_top_n = 5`
  (`RAG/rag_pipeline/config.py`) means only 5 chunks ever reach the model. A
  24-page Linux PDF with commands on nearly every page came back with just
  the ~10 basics from pages 5 and 7. That's retrieval, not the LLM — the
  model faithfully reported what it was handed. A smarter design than "top 5
  always, everything if the query says 'all'" is wanted; not decided.
- **Citations always say "Chunk 0".** `chunk_index` resets per page (each
  PDF page is its own document and most fit in one chunk). Accurate but
  misleading.
- **The model doesn't know the system.** `SYSTEM_PROMPT` is two sentences
  with no mention of pipelines, so "what pipelines do you have?" gets an
  answer built from whatever RAG retrieved (nothing relevant).
- **RAG and the knowledge graph don't talk to each other.** An uploaded
  document never becomes a graph node, so a document can be answerable in
  chat yet absent from `/graph`.
- **"view in graph ↗" on an answer** just navigates to `/graph` — no project
  scope, no highlighting of that turn's facts. (Agreed it should at least
  land on the right scope; the bigger per-turn highlight needs a new memory
  endpoint. Not built.)

### 17. Demo notes
**Memory vs no-memory demo** (what to show: the same question, better answer
when the graph has relevant facts):
1. As user A, state facts as plain *statements* (questions don't reliably
   trigger the write gate), e.g. "I'm Dr. Priya Nair, a cardiologist at
   Fortis Hospital." / "My patient Mr. Sharma has stage 2 hypertension." /
   "I prefer treatment plans that avoid beta-blockers when possible."
2. Wait about a minute (extraction runs in the background), then confirm the
   nodes on `/graph` *before* the real run.
3. Ask the same question from a fresh account (generic answer, no "Recalled
   facts" panel) and from user A in a **new chat** (personalized, panel
   shows). A new chat matters: in the same chat it's just visible history.
4. Keep uploads out of this segment. RAG is scoped now, but any upload within
   a user's own scope still flips that user's chats to the RAG route.

**Resetting everything:** `docker compose down -v` then `./start.sh` —
wipes Postgres, Neo4j and Qdrant; `docker/postgres-init.sh` replays all
migrations and the seed. Accounts are wiped too, so sign up again. To reset
just one user's graph: `DELETE /graph?user_id=<uuid>` on the memory service
(add `&project_id=` for project-tier facts).

## Current state

- **Backend**: 201 tests passing, 1 skipped — core 121, memory 48, RAG 32.
  The skip is a live-network test gated behind `RUN_LIVE_TESTS=1`. **Gotcha:**
  the ~62 database-backed tests *silently skip* when Postgres isn't running,
  so a run reporting ~60 skips means "start the stack" (`docker compose up -d`),
  not "all good". Frontend typechecks clean (`npx tsc --noEmit`).
- **Auth**: real signup/login/logout/Google OAuth, working end-to-end,
  including the frontend.
- **Chat**: working end-to-end against Gemini (models per §13), with real
  conversational context (each chat replays its own last 40 messages into
  the prompt — genuinely a per-conversation context window, same idea as
  ChatGPT/Claude), model-generated titles, and full chat/project management
  (rename, pin, move, delete). Chats can be unfiled.
- **Memory (knowledge graph)**: working end-to-end against Gemini —
  extraction, storage, and search all confirmed live. Per-user, and
  per-project within a user.
- **RAG**: wired into the answering flow and now scoped per user/project
  (§15). Routes to RAG only when *that* user's (or project's) documents
  exist.
- **Web search**: self-hosted SearXNG, wired into `core`, explicit opt-in
  only (§14).
- **Infrastructure**: one `docker-compose.yml`, one `start.sh`, documented
  in `README.md` and `RUNBOOK.md`.

## Next up (see `steps-to-implement-Bhoumish.md` for the live version)

Done since the last update: ~~memory `OPENAI_BASE_URL` support~~ (09-03),
~~port SearXNG into `core`~~ (09-03), ~~retire `orchestrator/`~~ (09-03),
~~per-user RAG scoping~~ (09-04).

1. Give the memory service real session-cookie auth of its own (prompt
   already drafted for that teammate). The RAG API now has the identical
   gap — it trusts the `user_id` it's handed — so whatever approach gets
   chosen should cover both.
2. "View in graph" from an answer should land on the right scope (small), and
   ideally highlight that turn's facts (needs a new memory endpoint). Agreed
   to build; not started.
3. A smarter retrieval strategy for "list everything" questions than a
   fixed top-5 (§16). Needs a design pass before any code.
4. Hybrid routing: the 2026-08-10 decision was cheap rules first, then
   tool-calling for ambiguous cases — i.e. give the main chat model
   `search_documents` / `search_web` / `search_memory` tools instead of the
   orchestrator pre-deciding. Build incrementally, one tool at a time.
   First check whether Gemini's OpenAI-compatible endpoint supports tool
   calls. Also worth a line in `SYSTEM_PROMPT` so the model knows what it has
   access to.
5. RAG-ingested documents becoming graph nodes (or the reverse). A
   `remember_this` tool, once tool-calling exists, is a plausible bridge.
6. Web-search privacy: redact client-identifying details before a query
   leaves the machine (open flag #1, §14).
7. Eventually: swap to a local model. Already fully config-driven on both
   `core`'s and `memory`'s sides (`OPENAI_BASE_URL` + the relevant model
   name(s), no code change) — and `memory`'s `OpenAIGenericClient` swap
   above is actually a point in favor here, not just a Gemini workaround:
   most local OpenAI-compatible servers (Ollama, LM Studio, vLLM) implement
   the older Chat Completions API, not necessarily OpenAI's newer Responses
   API, so the client that had to be used for Gemini is also the more
   broadly-compatible one for a local server. The one extra consideration
   for a *local embedding* model specifically: `EMBEDDING_DIM` is pinned to
   1536 to match OpenAI's `text-embedding-3-small`; a local embedding model
   that natively outputs a different size would need that constant re-pinned
   to match (or a model chosen that supports truncating down to 1536, the
   way both OpenAI's and Gemini's embedding models do). This is also the
   step that makes "fully local" literally true rather than a config
   change away.
8. EC2 deployment prep for the demo: Dockerfiles for the app services
   (currently only the databases are containerized), secrets management,
   CORS origins for production, port exposure restrictions.
