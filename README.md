# Nexus

Nexus is a local-first AI orchestration platform for building persistent memory, project context, and graph-based knowledge around human work. It combines a Next.js frontend with a Python memory service backed by Neo4j and Graphiti to turn conversations into structured, queryable relationships.

## Why Nexus

Modern AI tools often forget context between chats, sessions, and projects. Nexus keeps a live memory layer so conversations can evolve into a reusable knowledge graph rather than a flat transcript.

Core ideas:
- Local-first demo experience for AI workflows
- Persistent memory for users and projects
- Live knowledge graph visualization
- Story-driven context accumulation from chat messages
- Built for experimentation and product prototyping

## Features

- Real-time graph updates from chat ingestion
- Project-scoped memory groups
- Neo4j-backed knowledge graph storage
- FastAPI memory service with WebSocket streaming
- Interactive frontend for chat, graph exploration, and demo flows
- Seed script for building demo knowledge graphs quickly

## Architecture

- Frontend: Next.js app in `frontend/`
- Memory API: FastAPI service in `memory/app/`
- Graph database: Neo4j via Docker
- LLM + embeddings: Graphiti + OpenAI/Groq configuration

## Project structure

```text
TheProject/
├── .env.example
├── docker-compose.yml
├── start.sh
├── seed_graph.py
├── pyproject.toml
├── frontend/
│   ├── src/
│   ├── package.json
│   └── README.md
├── memory/
│   ├── app/
│   └── pyproject.toml
└── .github/
```

## Requirements

Before starting the app, make sure you have:

- Node.js 20+
- Python 3.12+
- Docker Desktop (or Docker Engine)
- API keys for at least:
  - `OPENAI_API_KEY`
  - `GROQ_API_KEY`

## Quick start

1. Copy the environment template:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

2. Fill in your API keys and Neo4j credentials in `.env`.

Example:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=nexus-local
OPENAI_API_KEY=your_openai_key
GROQ_API_KEY=your_groq_key
```

3. Start Neo4j and the memory service:

```bash
./start.sh
```

This script starts Dockerized Neo4j and launches the Python memory server on `http://localhost:8000`.

4. In a second terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev
```

The app will be available at:

- Frontend: http://localhost:3000
- Memory API: http://localhost:8000
- Neo4j Browser: http://localhost:7474

## Running manually

If you want to start each layer individually:

```bash
# terminal 1: Neo4j
docker compose up -d

# terminal 2: memory service
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e memory
cd memory
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# terminal 3: frontend
cd frontend
npm install
npm run dev
```

## Demo flow

A helper script is included to seed a demo graph:

```bash
python seed_graph.py
```

This submits a set of example messages to the memory service and builds a knowledge graph around Nexus Labs and its teams, projects, and relationships.

You can then visit:

- `http://localhost:3000/demo` for interactive demo prompts
- `http://localhost:3000/graph` for the live knowledge graph view

## API overview

The memory service exposes a few core endpoints:

- `POST /ingest` — add a text message to the graph memory
- `GET /graph` — fetch the graph for a user or project
- `DELETE /graph` — reset graph memory for a scope
- `GET /health` — health and Neo4j connectivity check
- `WS /ws/graph/{user_id}` — live graph updates via WebSocket

## License

This project is provided as-is for local experimentation and prototype development.

## Notes

Nexus is designed as a research-style product scaffold rather than a production-ready SaaS backend. It is best suited for exploring:

- contextual AI memory
- graph-based reasoning
- multi-project knowledge organization
- local-first deployment patterns
