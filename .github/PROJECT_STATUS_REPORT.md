# Nexus Project Status Report

Date: 2026-08-09
Branch reviewed: `feature/rag-pipeline`

> **Naming note (2026-09-02):** every `controller/` / "RAG controller"
> reference below is historical — that folder was moved and renamed to
> `RAG/rag_api.py` on 2026-09-02 (the name was confusing, sounding like "the
> central brain" when it's really just RAG's own front door). Left as-is
> here rather than rewritten, since this document is a point-in-time
> snapshot of what was true when it was written. Current details live in
> `.github/RAG_API_SERVICE.md`.

## Current Summary

The project currently has three major parts:

1. A Next.js frontend in `frontend/`.
2. A knowledge graph memory service in `memory/`, backed by Neo4j and Graphiti.
3. A RAG pipeline in `RAG/`, backed by Qdrant, with a small FastAPI controller in `controller/`.

The `feature/rag-pipeline` branch includes the code from `origin/main`, so the knowledge graph feature and the RAG feature are both present on this branch.

Remote `main` has not been updated with the RAG work. GitHub rejected a direct push to `main` because repository rules require changes to go through a pull request.

## Branch And Git Status

Current working branch:

```bash
feature/rag-pipeline
```

Relevant commits now on the branch:

```text
ac9de21 fix: resolve rag demo build issues
9edcfa8 Merge remote-tracking branch 'origin/main' into feature/rag-pipeline
31afead feat: add rag demo controller
7dc01aa Merge pull request #1 from Valgaza/feature/knowledge-graph
6796847 feat: add live knowledge-graph component and refactor frontend pages
89dfe05 feat: add knowledge graph seeding script
c698eb7 feat: add FastAPI memory service with Neo4j-backed graph app and checkpoints
83d9ad1 chore: add Docker Compose, env template, and Python project scaffolding
0307b6a added bacl code for rag
```

Important note:

- `origin/feature/rag-pipeline` has been pushed.
- `origin/main` has not been updated with the RAG changes.
- Local `main` was fast-forwarded during integration work, but the remote `main` push was rejected by GitHub branch protection.

## What Has Been Built

### Frontend

Location:

```text
frontend/
```

Framework:

```text
Next.js 16, React 19, TypeScript
```

Current routes:

```text
/
/chat
/conversation
/demo
/graph
/projects
/rag-demo
/settings
```

Key files:

```text
frontend/src/components/shell.tsx
frontend/src/components/live-graph.tsx
frontend/src/app/demo/page.tsx
frontend/src/app/rag-demo/page.tsx
frontend/src/styles/pages.css
frontend/src/styles/tokens.css
```

The frontend shell contains the main sidebar navigation. It currently has entries for:

- Live demo
- New chat
- Projects
- Knowledge graph
- RAG Demo
- Settings

The `LiveGraph` component connects to the memory service and displays the user's knowledge graph. It fetches graph data over HTTP and listens for graph updates over WebSocket.

The `/rag-demo` page is a separate screen for uploading documents, ingesting them into Qdrant, asking questions, and viewing the assembled LLM prompt plus sources.

## Why It Is Called `RAG Demo`

The name `RAG Demo` exists because this UI was created as a temporary integration surface for testing the RAG pipeline from the browser.

It is not a good final product name.

Better future names could be:

- Retrieval
- Knowledge Retrieval
- Document Search
- Document Memory
- Sources
- Workspace Knowledge

Recommendation:

Rename `RAG Demo` once we decide the final product concept. If the feature is meant to be user-facing, `Document Memory` or `Knowledge Retrieval` is clearer than `RAG Demo`.

## Knowledge Graph Feature

Location:

```text
memory/
```

Main service file:

```text
memory/app/main.py
```

Config file:

```text
memory/app/config.py
```

Supporting infrastructure:

```text
docker-compose.yml
start.sh
.env.example
seed_graph.py
```

Technology:

- FastAPI
- Neo4j
- Graphiti
- OpenAI embeddings (cloud, currently hard-required)
- `graphiti_core.llm_client.openai_client.OpenAIClient`, hardcoded to OpenAI's cloud API
- WebSocket updates

Main API endpoints:

```text
POST   /ingest
GET    /graph
DELETE /graph
GET    /health
WS     /ws/graph/{user_id}
```

Current behavior:

- The frontend sends chat-like text to `POST /ingest`.
- The memory service asks Graphiti to extract entities and relationships.
- Graphiti stores those entities and edges in Neo4j.
- The service broadcasts graph updates to connected frontend clients over WebSocket.
- The frontend renders the graph in `LiveGraph`.

Current default frontend memory URL:

```text
http://localhost:8000
```

Current important environment variables:

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=nexus-local
OPENAI_API_KEY=
GROQ_API_KEY=
```

Note:

`GROQ_API_KEY` appears in `.env.example`, but the current memory service code reads `OPENAI_API_KEY` and does not currently read `GROQ_API_KEY`. `memory/pyproject.toml` also depends on `graphiti-core[groq]`, so a Groq-based client was likely planned at some point but `memory/app/main.py` still hardcodes `OpenAIClient`. The swap was started but never finished.

Important note — cloud dependency conflicts with the project's core goal:

`memory/app/config.py` calls `_require("OPENAI_API_KEY")`, which raises at startup if the key is missing. This means the knowledge-graph service **cannot run at all without OpenAI's cloud API**, for both entity/relationship extraction and embeddings. This directly conflicts with the project's stated goal of running fully locally for institutions (medical, legal) that cannot rely on cloud-based LLMs. The RAG feature (below) has no such dependency — it is already fully local.

This is fixable without a rewrite: Graphiti also ships `graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient`, documented as targeting "any OpenAI-compatible `/chat/completions` endpoint (OpenAI, vLLM, llama.cpp, Ollama, DeepSeek, Together, etc.)" via a `base_url` on `LLMConfig`. `OpenAIEmbedderConfig` similarly accepts a `base_url` override. Swapping to a local runtime (e.g. Ollama or vLLM) is a client-and-config change, not new architecture — but which runtime to standardize on is still an open decision (see Open Engineering Questions).

## RAG Feature

Location:

```text
RAG/
```

Main package:

```text
RAG/rag_pipeline/
```

FastAPI controller:

```text
controller/main.py
```

Supporting files:

```text
RAG/pyproject.toml
RAG/env.example
RAG/docker-compose.yml
RAG/tests/
```

Technology:

- Python package named `rag-pipeline`
- Qdrant vector database
- FastEmbed dense embeddings
- Qdrant BM25 sparse embeddings
- Cross-encoder reranking
- Markdown and PDF document loading
- Context assembly for LLM prompts
- FastAPI controller for frontend integration

RAG package areas:

```text
chunking/
context/
embeddings/
loaders/
pipelines/
retrieval/
vectorstore/
```

Main controller endpoints:

```text
POST /api/ingest
POST /api/query
GET  /api/health
```

Current behavior:

- The frontend uploads a PDF or Markdown file.
- `controller/main.py` writes the upload to a temporary file.
- `IngestionPipeline` loads, chunks, embeds, and upserts the content to Qdrant.
- The frontend asks a question.
- `QueryPipeline` embeds the query, retrieves relevant chunks, reranks them, assembles context, and returns the assembled prompt and source metadata.

Important note — no answer-generation step exists yet:

`QueryPipeline.query()` (`RAG/rag_pipeline/pipelines/query.py`) stops after assembling the prompt. It never calls an LLM, local or cloud, to actually generate an answer — the `/rag-demo` page just displays the assembled prompt and sources as text. There are no OpenAI/Anthropic/cloud imports anywhere in `RAG/rag_pipeline`, and embeddings, sparse search, and reranking are all local already (FastEmbed, Qdrant BM25, a local cross-encoder). The one missing piece to make this pipeline actually answer questions is a local inference call at the end of `QueryPipeline.query()`. Separately, `frontend/src/app/conversation/page.tsx` currently fakes streaming responses with a `setTimeout` and has no real chat loop wired to any backend model.

Current default frontend RAG URL:

```text
http://localhost:8001
```

## Port Conflict — Resolved (2026-08-09)

Both backend services used to default to port `8000`, which meant they couldn't run at the same time.

Fixed by:

- Keeping the memory service on `8000` (unchanged).
- Moving the RAG controller to `8001`.
- `frontend/src/app/rag-demo/page.tsx` now reads `process.env.NEXT_PUBLIC_RAG_URL`, falling back to `http://localhost:8001`, matching the existing pattern already used for `NEXT_PUBLIC_MEMORY_URL` in `demo/page.tsx`, `conversation/page.tsx`, and `live-graph.tsx`.

No frontend `.env` template was added — none of the existing `NEXT_PUBLIC_MEMORY_URL` usages have one either; the code fallback is the documentation for local dev, and anyone who wants to override it can add a git-ignored `frontend/.env.local` (note: `frontend/.gitignore` ignores everything matching `.env*`, so a committed template would need an explicit `!` exception — not needed here since we're following the existing no-template convention).

Remaining follow-up (not yet done):

Create a root-level developer startup script that starts Neo4j, Qdrant, memory service, RAG controller, and frontend in a predictable way.

## How To Run The Project

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

### Knowledge Graph Service

From the repository root:

```bash
cp .env.example .env
```

Fill in required keys in `.env`.

Then run:

```bash
./start.sh
```

This starts Neo4j through Docker Compose and runs the memory service.

### RAG Service

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

## Verification So Far

Frontend build:

```bash
cd frontend
npm run build
```

Result:

```text
Passed
```

Frontend lint:

```bash
cd frontend
npm run lint
```

Result:

```text
Passed with warnings
```

Warnings currently include:

- Unused `streaming` variable in `frontend/src/app/conversation/page.tsx`.
- Font loading warning in `frontend/src/app/layout.tsx`.
- Unused imports in `frontend/src/components/app-shell.tsx`.
- One unused-expression warning in `frontend/src/app/page.tsx`.

RAG tests:

```bash
python -m pytest RAG/tests
```

Result:

```text
27 passed
```

Note:

The RAG test suite needs network access the first time it loads embedding/reranker models.

## Known Issues And Technical Debt

The two items below are more urgent than the rest of this section — they affect whether the system can meet its core "no cloud LLM" requirement and whether it can answer a question at all, not just naming or dev ergonomics.

### Knowledge Graph Service Requires Cloud OpenAI

`memory/app/config.py` hard-requires `OPENAI_API_KEY` at startup; the graph service cannot run without it. This conflicts with the project's goal of running fully locally for clients (medical, legal) that cannot use cloud LLMs.

Decision needed:

Which local LLM/embedding runtime should the project standardize on (e.g. Ollama, vLLM, llama.cpp)? Graphiti supports this already via `OpenAIGenericClient` + a `base_url` override, so this is a config/client swap, not a rewrite — but the runtime choice affects both this service and the RAG answer-generation gap below, so it's worth deciding once.

### No Answer-Generation Step In The RAG Pipeline

`QueryPipeline.query()` retrieves, reranks, and assembles a prompt, then stops. Nothing calls an LLM to turn that into an answer. The `/rag-demo` page only shows the raw assembled prompt and sources.

Decision needed:

Once a local LLM runtime is chosen (see above), wire a final generation call into `QueryPipeline.query()` (or a thin layer above it) so the assembled context actually produces an answer.

### Service Naming

`RAG Demo` and `RAG Demo Controller` are temporary names. They communicate implementation details instead of product behavior.

Decision needed:

Should this feature be presented as document retrieval, document memory, source-grounded answers, or something else?

### Hardcoded Frontend URLs — Resolved (2026-08-09)

`frontend/src/app/rag-demo/page.tsx` now reads:

```ts
const API_BASE = process.env.NEXT_PUBLIC_RAG_URL ?? "http://localhost:8001";
```

### Backend Service Split

There are currently two separate FastAPI services:

- `memory/app/main.py`
- `controller/main.py`

This is fine during development, but the long-term architecture needs a decision:

1. Keep them separate services.
2. Combine them into one backend API.
3. Keep separate internal modules but expose one gateway API to the frontend.

Recommendation:

Keep them separate for now while the architecture is still evolving. Later, consider a gateway or single backend only after the behavior stabilizes.

### Generated Files Tracked In Git — Decided (2026-08-10)

Decision: removed `RAG/rag_pipeline.egg-info/` from git tracking and added `*.egg-info/` to the root `.gitignore`. Files remain on disk (still needed locally by the editable install), just no longer tracked. Staged locally, not yet committed.

### Sample PDF In Git — Decided (2026-08-09)

`RAG/sample_docs/TheProject_Idea.pdf` is committed.

Decision: keep it. Intended to stay in the repo as a sample document for the RAG pipeline.

### Secrets

`RAG/.env` exists locally and is ignored by Git.

This is good.

Do not commit real `.env` files.

## Recommended Next Steps

1. Choose a local LLM runtime (e.g. Ollama, vLLM, llama.cpp) to serve both graph extraction/embeddings and RAG answer generation.
2. Swap `memory/app/main.py` from `OpenAIClient`/`OpenAIEmbedder` pointed at OpenAI's cloud to `OpenAIGenericClient`/a local `base_url`, so the graph service no longer requires `OPENAI_API_KEY`.
3. Wire a final generation call into `QueryPipeline.query()` so RAG queries produce an actual answer, not just an assembled prompt.
4. ~~Decide final port assignment.~~ Done — RAG controller moved to `8001`.
5. ~~Move RAG controller to `8001` and update frontend config.~~ Done.
6. Rename `RAG Demo` to a product-facing name.
7. Add clear developer run documentation. *(in progress)*
8. Decide whether to remove tracked generated package metadata.
9. Decide whether the RAG and knowledge graph features should remain separate workflows or become one combined "memory" experience.

## Open Engineering Questions

1. Which local LLM runtime should the project standardize on, and does it need to serve both chat generation and Graphiti's extraction, or can those differ?
2. Should user-uploaded documents become nodes in the knowledge graph, or should RAG and graph stay separate?
3. Should graph extraction run over RAG-ingested documents, chat messages, or both?
4. Should the chat experience answer with retrieved sources, graph memory, or a combined context from both?
5. Should Qdrant and Neo4j both be long-term dependencies, or is one temporary?
6. What is the user-facing product concept: "Nexus memory", "document search", "knowledge workspace", or something else?

