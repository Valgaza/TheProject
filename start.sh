#!/usr/bin/env bash
# Starts the whole Nexus backend: Neo4j, the memory service and the core
# service. Run the frontend separately: cd frontend && npm run dev
#
# Both services run under one uvicorn each, deliberately. The memory service's
# WebSocket subscriptions and background ingests are in-process, so --workers > 1
# would give each worker a different half of the connected clients.
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

# ── Neo4j ────────────────────────────────────────────────────
echo "▶  Starting Neo4j..."
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

# ── Supabase ─────────────────────────────────────────────────
# Not started here: the Supabase CLI owns those containers and `supabase start`
# is slow and chatty. Checked, though, because the core service is useless
# without Postgres and the failure is otherwise a wall of asyncpg traceback.
if ! curl -sf "http://localhost:54321/rest/v1/" >/dev/null 2>&1 \
   && ! docker ps --format '{{.Names}}' | grep -q '^supabase_db_'; then
  echo "✗  Supabase is not running. In another terminal:"
  echo "     supabase start"
  echo "   First time only, after that:  supabase db reset"
  exit 1
fi
echo "▶  Supabase is up."

# ── Services ─────────────────────────────────────────────────
pids=()
cleanup() {
  echo ""
  echo "── Stopping services ───────────────────────────────────"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "   Neo4j is still running. Stop it with: docker compose down"
}
trap cleanup INT TERM EXIT

echo "▶  memory service  →  http://localhost:8000"
"$VENV/uvicorn" nexus_memory.main:app --reload --port 8000 &
pids+=($!)

echo "▶  core service    →  http://localhost:8100"
"$VENV/uvicorn" nexus_core.main:app --reload --port 8100 &
pids+=($!)

echo ""
echo "   Frontend:  cd frontend && npm run dev  →  http://localhost:3000"
echo "   Press Ctrl+C to stop both services."
echo "────────────────────────────────────────────────────────"

wait
