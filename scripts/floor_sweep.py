"""Re-measure memory's similarity floor (MEMORY_SEARCH_MIN_SCORE).

The floor is a cosine threshold, and cosine scores are only comparable within
one embedding model. 0.45 was measured on nomic-embed-text (see the sweep in
memory/nexus_memory/retrieval.py); a different model spreads its scores
differently, so the floor has to be measured again whenever
MEMORY_EMBEDDING_MODEL changes.

Same shape as the original sweep: a small corpus of facts about one person,
then queries of three kinds -- direct ("what languages do I write in?"),
oblique ("remind me what my name is"), and unrelated -- searched at a range of
floors. A hit is the expected fact coming back; noise is anything at all
coming back for an unrelated query. The knee is the lowest floor past which
raising it only loses hits.

    uv run python scripts/floor_sweep.py

Needs memory and the inference server up. Deletes what it wrote.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SUBJECT = "Mira Castell"

CORPUS = [
    f"{SUBJECT} writes mostly in Rust and Python.",
    f"{SUBJECT} prefers short answers without bullet points.",
    f"{SUBJECT} is building a sensor dashboard backed by TimescaleDB.",
    f"{SUBJECT} lives in Lisbon.",
    f"{SUBJECT}'s name is pronounced MEE-ra.",
]

# (kind, query, a word the expected fact must contain; None = expect nothing)
QUERIES = [
    ("direct", "What programming languages do I write in?", "rust"),
    ("direct", "Which database is my dashboard using?", "timescale"),
    ("direct", "Where do I live?", "lisbon"),
    ("oblique", "Remind me what my name is.", "mira"),
    ("oblique", "How should you format replies for me?", "bullet"),
    ("oblique", "What was that project I mentioned?", "dashboard"),
    ("unrelated", "What is the boiling point of water at sea level?", None),
    ("unrelated", "Recommend a good fantasy novel.", None),
]

FLOORS = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args()
    env = dotenv_values(ROOT / ".env")
    base = env.get("MEMORY_SERVICE_URL") or "http://localhost:8000"
    headers = {"X-Service-Token": env.get("SERVICE_TOKEN") or ""}
    user_id = str(uuid.uuid4())
    print(f"embedding model {(os.environ.get('MEMORY_EMBEDDING_MODEL') or env.get('MEMORY_EMBEDDING_MODEL'))}, throwaway user {user_id}", flush=True)

    rows = []
    with httpx.Client(base_url=base, headers=headers, timeout=600) as http:
        try:
            for fact in CORPUS:
                status = http.post("/ingest", json={
                    "text": fact, "user_id": user_id, "scope": "user", "wait": True,
                }).json().get("status")
                print(f"  {status:<9} {fact}", flush=True)
            for floor in FLOORS:
                hit = missed = noisy = 0
                for kind, query, expect in QUERIES:
                    facts = [f["fact"].lower() for f in http.post("/search", json={
                        "query": query, "user_id": user_id, "min_score": floor,
                    }).json()["facts"]]
                    if expect is None:
                        noisy += bool(facts)
                    elif any(expect in f for f in facts):
                        hit += 1
                    else:
                        missed += 1
                rows.append((floor, hit, missed, noisy))
        finally:
            http.delete("/graph", params={"user_id": user_id, "view": "all"})

    print("\n| Floor | Hit | Missed | Noisy |\n|---|---|---|---|")
    for floor, hit, missed, noisy in rows:
        print(f"| {floor:.2f} | {hit} | {missed} | {noisy} |")
    best = max(hit for _, hit, _, _ in rows)
    knee = max(floor for floor, hit, _, _ in rows if hit == best)
    print(f"\nHighest floor that keeps every achievable hit ({best} of 6): {knee:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
