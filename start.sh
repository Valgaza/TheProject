#!/usr/bin/env bash
# Starts the whole Nexus backend: Neo4j, the memory service and the core
# service. Run the frontend separately: cd frontend && npm run dev
#
# Both services run under one uvicorn each, deliberately. Four things in the
# memory service are in-process state, so --workers > 1 would give each worker
# its own copy and break all of them:
#
#   1. the WebSocket ConnectionManager  — half the clients per worker
#   2. BackgroundTasks                  — ingests scattered across workers
#   3. hygiene.group_lock               — two workers sweeping and ingesting
#                                         the same group at once
#   4. hygiene._suppressed              — duplicate episodes reappearing in
#                                         search on whichever worker missed
#                                         the sweep
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin"

echo "── Nexus startup ───────────────────────────────────────"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "✗  .env not found — copy and fill in your keys:"
  echo "   cp $ROOT/.env.example $ROOT/.env"
  exit 1
fi

if ! command -v docker &>/dev/null; then
  echo "✗  Docker not found — install Docker Desktop and try again."
  exit 1
fi

if [[ ! -x "$VENV/uvicorn" ]]; then
  echo "✗  No virtualenv at $ROOT/.venv — run: uv sync"
  exit 1
fi

# ── Infrastructure ───────────────────────────────────────────
# One compose file now starts everything: Postgres (chat transcripts), Neo4j
# (the graph), Qdrant (document vectors) and SearXNG (private web search).
# Replaced the Supabase CLI on 2026-09-02 — it started twelve containers for
# the one Postgres this project actually uses. The schema is applied
# automatically the first time the Postgres volume is created; see
# docker/postgres-init.sh.
echo "▶  Starting infrastructure (Postgres, Neo4j, Qdrant, SearXNG)..."
docker compose -f "$ROOT/docker-compose.yml" up -d

printf "   Waiting for Neo4j"
# Bounded, because an unbounded poll on a container that will never come up
# hangs this script forever with no clue as to why.
for _ in $(seq 1 40); do
  if curl -sf "http://localhost:7474/" >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  printf "."
  sleep 3
done
if ! curl -sf "http://localhost:7474/" >/dev/null 2>&1; then
  echo ""
  echo "✗  Neo4j did not come up within 2 minutes."
  echo "   Check: docker logs nexus-neo4j"
  exit 1
fi

printf "   Waiting for Postgres"
# Checked separately from the compose healthcheck, because the core service is
# useless without Postgres and the failure is otherwise a wall of asyncpg
# traceback thirty seconds after everything looked fine.
for _ in $(seq 1 40); do
  if docker exec nexus-postgres pg_isready -U postgres -q >/dev/null 2>&1; then
    echo " ready."
    break
  fi
  printf "."
  sleep 2
done
if ! docker exec nexus-postgres pg_isready -U postgres -q >/dev/null 2>&1; then
  echo ""
  echo "✗  Postgres did not come up within 80 seconds."
  echo "   Check: docker logs nexus-postgres"
  exit 1
fi

# ── Services ─────────────────────────────────────────────────
pids=()
cleanup() {
  echo ""
  echo "── Stopping services ───────────────────────────────────"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "   Containers are still running. Stop them with: docker compose down"
}
trap cleanup INT TERM EXIT

echo "▶  memory service  →  http://localhost:8000"
"$VENV/uvicorn" nexus_memory.main:app --reload --port 8000 &
pids+=($!)

# RAG's own HTTP front door (formerly controller/main.py). core reaches the
# RAG pipeline only through this — they are separate processes, so there is no
# other way in. Added here 2026-09-02; on branches without RAG/ it simply
# isn't there to start.
echo "▶  RAG API         →  http://localhost:8001"
"$VENV/uvicorn" rag_api:app --app-dir "$ROOT/RAG" --reload --port 8001 &
pids+=($!)

echo "▶  core service    →  http://localhost:8100"
"$VENV/uvicorn" nexus_core.main:app --reload --port 8100 &
pids+=($!)

echo ""
echo "   Frontend:  cd frontend && npm run dev  →  http://localhost:3000"
echo "   Press Ctrl+C to stop all three services."
echo "────────────────────────────────────────────────────────"

wait
