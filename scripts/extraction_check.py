"""How well the current model builds the knowledge graph.

Small models usually fail at graph extraction before they fail at chat:
graphiti asks for structured JSON (entities, then relationships between them)
and a weak model drops entities, invents odd ones, or writes facts nothing
can match. The runtime's schema-constrained decoding guarantees the JSON is
well-formed; this measures whether its *content* is any good.

Feeds a fixed set of statements to the memory service, under a throwaway
user, one episode at a time (as the write gate does), then reads the graph
back and scores it against a hand-written expectation:

- entity recall: expected entity names present as nodes
- fact recall:   statements whose expected relationship shows up as an edge fact
- failures:      episodes that did not ingest at all
- time:          seconds per episode

    uv run python scripts/extraction_check.py --out docs/benchmarks/extraction-qwen3-8b

Needs memory and the runtime up. Deletes everything it wrote when done.
"""

from __future__ import annotations

import argparse
import os
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

# (statement, entities that must appear as nodes, any one keyword set that
# must all appear in a single edge fact)
CASES: list[tuple[str, list[str], list[list[str]]]] = [
    ("Aarya Patil is building Nexus, a local-first assistant, as a B.Tech project.",
     ["Aarya", "Nexus"], [["aarya", "nexus"]]),
    ("Nexus stores what it learns about people in Neo4j through graphiti.",
     ["Nexus", "Neo4j"], [["nexus", "neo4j"]]),
    ("Aarya prefers terse answers without bullet points.",
     ["Aarya"], [["terse"], ["bullet"]]),
    ("Bhoumish owns the RAG pipeline in the Nexus team.",
     ["Bhoumish", "RAG"], [["bhoumish", "rag"]]),
    ("In October 2026 Aarya moved Nexus's inference from Ollama to MLX.",
     ["Ollama", "MLX"], [["mlx"], ["ollama"]]),
    ("Qdrant holds the document vectors for Nexus.",
     ["Qdrant"], [["qdrant"]]),
    ("Aarya's laptop is an M5 Pro with 24 GB of unified memory.",
     ["M5 Pro"], [["m5"], ["24 gb"], ["24gb"]]),
    ("Siya is building the vision pipeline with PaddleOCR.",
     ["Siya", "PaddleOCR"], [["siya", "vision"], ["paddleocr"]]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    base = env.get("MEMORY_SERVICE_URL") or "http://localhost:8000"
    headers = {"X-Service-Token": env.get("SERVICE_TOKEN") or ""}
    user_id = str(uuid.uuid4())
    model = (os.environ.get("MEMORY_EXTRACTION_MODEL") or env.get("MEMORY_EXTRACTION_MODEL"))
    print(f"extraction model {model}, throwaway user {user_id}", flush=True)

    results = []
    with httpx.Client(base_url=base, headers=headers, timeout=600) as http:
        try:
            for statement, _, _ in CASES:
                t0 = time.perf_counter()
                body = http.post("/ingest", json={
                    "text": statement, "user_id": user_id, "scope": "user", "wait": True,
                }).json()
                seconds = time.perf_counter() - t0
                results.append({"statement": statement, "status": body.get("status"),
                                "error": body.get("error"), "seconds": round(seconds, 1)})
                print(f"  {body.get('status'):<9} {seconds:6.1f}s  {statement[:60]}", flush=True)

            graph = http.get("/graph", params={"user_id": user_id, "view": "user"}).json()
        finally:
            http.delete("/graph", params={"user_id": user_id, "view": "all"})

    names = [n["name"].lower() for n in graph["nodes"]]
    facts = [e["fact"].lower() for e in graph["edges"]]
    expected_entities = [e for _, ents, _ in CASES for e in ents]
    found_entities = [e for e in expected_entities if any(e.lower() in n for n in names)]
    fact_hits = [
        statement for statement, _, keysets in CASES
        if any(all(k in f for k in keys) for keys in keysets for f in facts)
    ]
    times = [r["seconds"] for r in results]
    report = {
        "model": model,
        "episodes": len(results),
        "failed": [r for r in results if r["status"] != "ingested"],
        "entity_recall": round(len(found_entities) / len(expected_entities), 3),
        "missing_entities": sorted(set(expected_entities) - set(found_entities)),
        "fact_recall": round(len(fact_hits) / len(CASES), 3),
        "statements_without_a_matching_fact": [s for s, _, _ in CASES if s not in fact_hits],
        "seconds_per_episode": {"p50": statistics.median(times), "max": max(times)},
        "nodes": sorted(set(names)),
        "facts": facts,
        "results": results,
    }

    md = "\n".join([
        f"# Extraction check: `{model}`", "",
        f"- Episodes: {len(results)}, failed: {len(report['failed'])}",
        f"- Entity recall: {report['entity_recall']:.0%} (missing: {', '.join(report['missing_entities']) or 'none'})",
        f"- Fact recall: {report['fact_recall']:.0%}",
        f"- Seconds per episode: p50 {report['seconds_per_episode']['p50']:.1f}, "
        f"max {report['seconds_per_episode']['max']:.1f}",
        "", "## Facts extracted", "", *[f"- {f}" for f in facts], "",
    ])
    print("\n" + md)
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        (out.parent / f"{out.name}.json").write_text(json.dumps(report, indent=2))
        (out.parent / f"{out.name}.md").write_text(md)
        print(f"wrote {(out.parent / f"{out.name}.md")}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
