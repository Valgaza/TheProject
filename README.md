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
