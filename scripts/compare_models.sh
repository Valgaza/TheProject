#!/usr/bin/env bash
# Run the same measurements on each candidate chat model, one at a time.
#
#   scripts/compare_models.sh mlx-community/Qwen3.5-9B-MLX-4bit [more ids...]
#
# For each model: point every LLM role in .env at it (chat, gate, graph
# extraction: one resident model does all three), start the stack, and run
#   - scripts/bench.py           text latency and memory writes
#   - scripts/extraction_check.py graph extraction quality
#   - scripts/voice_bench.py     spoken latency (10 exchanges)
#   - the scenario suite         behaviour (pass/fail)
# Reports go to docs/benchmarks/candidates/<model>-*. .env is restored at the
# end, whatever happens. The stack must be stopped before running this.
# Models must already be downloaded: the runtime is started offline.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/benchmarks/candidates"
mkdir -p "$OUT" "$ROOT/data/benchmarks"
cp "$ROOT/.env" "$ROOT/.env.compare-backup"
restore() { mv "$ROOT/.env.compare-backup" "$ROOT/.env"; }
trap restore EXIT

for model in "$@"; do
  name="$(basename "$model")"
  log="$ROOT/data/benchmarks/$name-stack.log"   # gitignored: several MB per run
  echo "══ $model"
  sed -i '' -E "s#^(CORE_CHAT_MODEL|CORE_GATE_MODEL|MEMORY_EXTRACTION_MODEL)=.*#\1=$model#" "$ROOT/.env"

  HF_HUB_OFFLINE=1 "$ROOT/start.sh" > "$log" 2>&1 &
  stack=$!
  for _ in $(seq 1 200); do
    curl -s localhost:8100/health 2>/dev/null | grep -q '"status":"ok"' \
      && curl -s localhost:8003/health 2>/dev/null | grep -q '"status":"ok"' && break
    kill -0 $stack 2>/dev/null || break
    sleep 3
  done
  if ! curl -s localhost:8003/health 2>/dev/null | grep -q '"status":"ok"'; then
    echo "   ✗ did not start; see $log"
    kill $stack 2>/dev/null; wait $stack 2>/dev/null
    continue
  fi
  curl -s localhost:8003/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('   runtime', d['active_memory_gb'], 'GB active')"

  cd "$ROOT"
  uv run python scripts/bench.py --rounds 2 --label "$name" --out "$OUT/$name-text" 2>&1 | tail -1
  uv run python scripts/extraction_check.py --out "$OUT/$name-extraction" 2>&1 | tail -3
  uv run python scripts/voice_bench.py --exchanges 10 --barge-every 0 --out "$OUT/$name-voice" 2>&1 | tail -1
  RUN_SCENARIOS=1 uv run pytest tests -q -rfEx -p no:warnings > "$OUT/$name-scenarios.txt" 2>&1
  grep -E "^(FAILED|ERROR|XFAIL)|passed|failed" "$OUT/$name-scenarios.txt"

  kill $stack 2>/dev/null; wait $stack 2>/dev/null
  sleep 5
done
