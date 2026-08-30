#!/usr/bin/env bash
# Starts Neo4j and the memory service for a Nexus demo.
# Run the frontend separately: cd frontend && npm run dev
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv/bin"

echo "── Nexus demo startup ──────────────────────────────────"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "✗  .env not found — copy and fill in your keys:"
  echo "   cp $ROOT/.env.example $ROOT/.env"
  exit 1
fi

if ! command -v docker &>/dev/null; then
  echo "✗  Docker not found — install Docker Desktop and try again."
  exit 1
fi

# ── Neo4j ────────────────────────────────────────────────────
echo "▶  Starting Neo4j..."
docker compose -f "$ROOT/docker-compose.yml" up -d

printf "   Waiting for Neo4j to be ready"
# Bounded, because an unbounded poll on a container that will never come up
# hangs this script forever with no clue as to why.
for _ in $(seq 1 40); do
  if curl -sf "http://localhost:7474/" >/dev/null 2>&1; then
    echo " done."
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

# ── Memory service ───────────────────────────────────────────
echo "▶  Starting memory service on http://localhost:8000"
echo "   Frontend: cd frontend && npm run dev  →  http://localhost:3000"
echo "   Press Ctrl+C to stop."
echo "────────────────────────────────────────────────────────"

# One worker, deliberately. The WebSocket ConnectionManager holds its
# subscriptions in memory and BackgroundTasks run in-process, so --workers > 1
# would give each worker a different half of the connected clients and drop
# graph updates on the floor. Scaling out needs a shared broker first.
cd "$ROOT/memory"
PYTHONPATH=. "$VENV/uvicorn" nexus_memory.main:app --reload --port 8000
