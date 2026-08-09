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
until curl -sf "http://localhost:7474/" >/dev/null 2>&1; do
  printf "."
  sleep 3
done
echo " done."

# ── Memory service ───────────────────────────────────────────
echo "▶  Starting memory service on http://localhost:8000"
echo "   Frontend: cd frontend && npm run dev  →  http://localhost:3000"
echo "   Press Ctrl+C to stop."
echo "────────────────────────────────────────────────────────"

cd "$ROOT/memory"
PYTHONPATH=. "$VENV/uvicorn" app.main:app --reload --port 8000
