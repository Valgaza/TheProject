"""Re-embed every vector in the knowledge graph with the current embedding model.

Similarity only means something between vectors from the same model. When
MEMORY_EMBEDDING_MODEL changes (nomic-embed-text on Ollama → EmbeddingGemma on
the MLX runtime, 2026-10), every name and fact embedding already in Neo4j
belongs to the old model's space, and search quietly compares unrelated
numbers. This recomputes them in place, exactly as graphiti would have:

- Entity and Community nodes: ``name_embedding`` from ``name``
- RELATES_TO edges: ``fact_embedding`` from ``fact``

(graphiti_core/nodes.py and edges.py embed those fields with newlines turned
into spaces; this does the same.)

    uv run python scripts/reembed_graph.py --dry-run   # count only
    uv run python scripts/reembed_graph.py

Needs Neo4j and the runtime up. Reads NEO4J_*, OPENAI_BASE_URL,
OPENAI_API_KEY and MEMORY_EMBEDDING_MODEL from the repo-root .env. Safe to run
twice: it overwrites, it never adds.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values
from neo4j import AsyncGraphDatabase

ROOT = Path(__file__).resolve().parent.parent
BATCH = 64

TARGETS = [
    # (label, match clause, text field, vector field, setter)
    ("Entity", "MATCH (x:Entity)", "name", "name_embedding",
     'CALL db.create.setNodeVectorProperty(x, "name_embedding", row.vec)'),
    ("Community", "MATCH (x:Community)", "name", "name_embedding",
     'CALL db.create.setNodeVectorProperty(x, "name_embedding", row.vec)'),
    ("RELATES_TO", "MATCH ()-[x:RELATES_TO]->()", "fact", "fact_embedding",
     'CALL db.create.setRelationshipVectorProperty(x, "fact_embedding", row.vec)'),
]


async def _embed(http: httpx.AsyncClient, model: str, texts: list[str]) -> list[list[float]]:
    response = await http.post("/embeddings", json={"model": model, "input": texts})
    response.raise_for_status()
    data = sorted(response.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would be re-embedded")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    model = env.get("MEMORY_EMBEDDING_MODEL")
    base_url = env.get("OPENAI_BASE_URL")
    if not (model and base_url):
        print("MEMORY_EMBEDDING_MODEL and OPENAI_BASE_URL must be set in .env", file=sys.stderr)
        return 2
    dim = int(env.get("EMBEDDING_DIM") or 768)

    driver = AsyncGraphDatabase.driver(
        env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])
    )
    headers = {"Authorization": f"Bearer {env.get('OPENAI_API_KEY', '')}"}
    async with driver, httpx.AsyncClient(base_url=base_url, headers=headers, timeout=120) as http:
        for label, match, text_field, vec_field, setter in TARGETS:
            records, _, _ = await driver.execute_query(
                f"{match} WHERE x.{text_field} IS NOT NULL "
                f"RETURN elementId(x) AS id, x.{text_field} AS text"
            )
            rows = [(r["id"], str(r["text"]).replace("\n", " ")) for r in records]
            print(f"{label:<11} {len(rows):>6} to re-embed", flush=True)
            if args.dry_run or not rows:
                continue
            for i in range(0, len(rows), BATCH):
                batch = rows[i : i + BATCH]
                vectors = await _embed(http, model, [text for _, text in batch])
                if any(len(v) != dim for v in vectors):
                    print(f"embedding model returned {len(vectors[0])} dims, EMBEDDING_DIM is {dim}",
                          file=sys.stderr)
                    return 1
                where = "elementId(x) = row.id"
                await driver.execute_query(
                    f"UNWIND $rows AS row {match} WHERE {where} WITH x, row {setter}",
                    rows=[{"id": id_, "vec": vec} for (id_, _), vec in zip(batch, vectors)],
                )
                print(f"  {min(i + BATCH, len(rows))}/{len(rows)}", flush=True)
    print("done" if not args.dry_run else "dry run: nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
