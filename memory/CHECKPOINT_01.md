# Checkpoint 01 — Infrastructure Setup

## Testing Instructions

### 1. Verify Neo4j is running and reachable

**Start the container** (from the repo root):
```bash
docker compose up -d
```

**Wait for the health check to pass** (~30 s):
```bash
docker compose ps
# STATUS column should read "healthy" for nexus-neo4j
```

**Check the HTTP browser interface:**
```bash
curl -s http://localhost:7474 | head -5
# Should return HTML (Neo4j Browser)
```

**Check the Bolt port is listening:**
```bash
nc -zv localhost 7687
# Should print: Connection to localhost port 7687 [tcp] succeeded!
```

**Log in via Neo4j Browser:**
Open `http://localhost:7474` in a browser. Connect with:
- Connection URL: `bolt://localhost:7687`
- Username: `neo4j`
- Password: `nexus-local` (or whatever `NEO4J_PASSWORD` is set to in your `.env`)

Run a quick Cypher smoke test:
```cypher
RETURN 1 AS ok
```

### 2. Verify the Python environment

**Confirm the virtual environment is active and packages are present:**
```bash
# From repo root
source .venv/bin/activate

python -c "
import fastapi, uvicorn, graphiti_core, neo4j, dotenv, openai
print('fastapi   ', fastapi.__version__)
print('uvicorn   ', uvicorn.__version__)
print('neo4j     ', neo4j.__version__)
print('openai    ', openai.__version__)
print('graphiti_core  OK')
print('dotenv         OK')
"
```

All six lines should print without errors.

**Confirm the memory package itself is installed:**
```bash
pip show nexus-memory
# Should list name, version, location
```

---

## Context Summary

### What was created

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Runs Neo4j 5.26-community with APOC plugin, ports 7474/7687, two named volumes |
| `.env.example` | Documents every env var the system needs; copy to `.env` and fill in secrets |
| `pyproject.toml` (root) | Declares the `nexus` uv workspace and registers `memory/` as a member |
| `memory/pyproject.toml` | Declares `nexus-memory` package with all backend dependencies |
| `memory/CHECKPOINT_01.md` | This file |

### Decisions made

**Package manager:** `uv` — already in use (`.venv` existed, `uv 0.10.0` installed). Workspace mode (`[tool.uv.workspace]`) wires the root and `memory/` together so a single `uv sync --all-packages` installs everything.

**Neo4j version:** `5.26-community` — latest stable 5.x community image at time of setup. APOC plugin included because graphiti-core uses it for graph operations.

**Neo4j ports:**
- `7474` — HTTP (Neo4j Browser UI)
- `7687` — Bolt (driver connections)

**Neo4j auth:** Controlled by `NEO4J_USERNAME` / `NEO4J_PASSWORD` env vars (defaulting to `neo4j` / `nexus-local`). The compose file constructs the `NEO4J_AUTH` value (`username/password`) that the container expects.

**Named volumes:** `nexus_neo4j_data` and `nexus_neo4j_logs` — data survives `docker compose down`; only `docker compose down -v` removes it.

**Installed package versions (resolved by uv):**

| Package | Version |
|---------|---------|
| fastapi | 0.136.1 |
| uvicorn | 0.47.0 |
| graphiti-core | 0.29.0 |
| neo4j (driver) | 6.2.0 |
| openai | 2.36.0 |
| groq | 1.2.0 |
| pydantic | 2.13.4 |
| python-dotenv | (via dotenv) |

**graphiti-core extras:** Installed as `graphiti-core[groq]` — pulls in the Groq client so graphiti can use Groq-hosted LLMs. OpenAI is separately installed for the default embedding model (`text-embedding-3-small`).

### Current project state

```
TheProject/
├── docker-compose.yml        ← NEW: Neo4j service
├── pyproject.toml            ← NEW: uv workspace root
├── .env.example              ← NEW: env var documentation
├── .venv/                    ← EXISTING: updated with all deps
├── frontend/                 ← EXISTING: Next.js App Router frontend
├── memory/
│   ├── pyproject.toml        ← NEW: memory service package config
│   └── CHECKPOINT_01.md      ← NEW: this file
├── pipelines/                ← EMPTY: future work
└── shared/                   ← EMPTY: future work
```

No application logic exists yet. The next step is to build the FastAPI application inside `memory/` using graphiti-core connected to the running Neo4j instance.
