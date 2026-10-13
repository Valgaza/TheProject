"""The fact ledger: every fact the write gate accepted, searchable by meaning.

Recall used to depend on graphiti's extraction. Graphiti's LLM turns a written
fact into entities and edges, and recall searched those. With the 8B model it
extracted 5 of about 20 facts in a scripted run (2026-10-13), and recall
answered 11 of 18 questions: "the thesis deadline is 15 December" was written,
confirmed, and never found, because it existed only as the raw episode, which
recall ranks last, cuts first, and searches by keyword only ("dietary
restrictions" can't find "vegetarian").

The gate already produces what recall needs: short, self-contained facts. So
each one is stored as it is, the moment it is written, as a ``NexusFact`` node
in the same Neo4j and the same tier (``group_id``), with an embedding.
Search ranks them by cosine similarity (EmbeddingGemma's own query/document
prompts, which separate right from wrong matches more cleanly), plus a small
bonus for the question's own words. The graph is still built from the same
facts, for relationships and the graph view; recall no longer waits on it or
depends on how well it was extracted.

What holds for the graph holds here: deleting a tier deletes its facts
(``group_id``); retiring a fact marks it (hygiene.retire) rather than deleting.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# EmbeddingGemma's prompts for retrieval. Measured on the scenario run's facts
# and questions: the median wrong match fell from 0.21 to 0.14 (p95 0.33 to
# 0.26) while right matches held at about 0.48.
QUERY_PROMPT = "task: search result | query: "
DOC_PROMPT = "title: none | text: "

# Below this nothing is "about" the question. Right matches start around 0.27;
# 95% of wrong ones are under 0.26.
MIN_SCORE = 0.28
KEYWORD_BONUS = 0.08  # per question word found in the fact, up to two

_STOP = set("""a an the is are was were be been am i me my mine we our you your he she they them it its of to in
on at for with and or but do does did have has had what which who whom whose when where why how this that these
those there here about can could would should will shall may might must not no yes any some""".split())

_UPSERT = """
UNWIND $facts AS f
MERGE (n:NexusFact {group_id: f.group_id, key: f.key})
ON CREATE SET n.uuid = f.uuid, n.statement = f.statement, n.embedding = f.embedding,
              n.created_at = datetime(f.created_at), n.episode_uuid = f.episode_uuid
RETURN n.uuid AS uuid
"""

_LIVE = """
MATCH (n:NexusFact) WHERE n.group_id IN $groups AND n.nexus_retired_at IS NULL
RETURN n.uuid AS uuid, n.group_id AS group_id, n.statement AS statement,
       n.embedding AS embedding, n.created_at AS created_at
"""


@dataclass(frozen=True)
class LedgerFact:
    uuid: str
    group_id: str
    statement: str
    score: float
    created_at: datetime | None


def _key(statement: str) -> str:
    return " ".join(statement.lower().split()).rstrip(".")


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def statements_of(text: str) -> list[str]:
    """The gate writes one fact per line."""
    return [line.strip() for line in text.splitlines() if line.strip()]


async def write(driver, embedder, group_id: str, statements: list[str], episode_uuid: str | None = None,
                created_at: datetime | None = None) -> list[str]:
    """Store each statement as a fact in ``group_id``. The same statement
    twice in one tier is stored once. ``created_at`` dates history written
    after the fact; otherwise it is now."""
    if not statements:
        return []
    vectors = await embedder.create_batch([DOC_PROMPT + s for s in statements])
    now = (created_at or datetime.now(timezone.utc)).isoformat()
    records, _, _ = await driver.execute_query(_UPSERT, facts=[
        {"group_id": group_id, "key": _key(s), "uuid": str(uuid.uuid4()), "statement": s,
         "embedding": list(v), "created_at": now, "episode_uuid": episode_uuid}
        for s, v in zip(statements, vectors)
    ])
    return [r["uuid"] for r in records]


def covered(text: str, keys: set[str]) -> bool:
    """Whether every line or sentence of ``text`` (an episode, an entity
    summary) is already a ledger fact, so returning it too only repeats."""
    parts = [p for p in re.split(r"\n|(?<=[.!?])\s+", text) if p.strip()]
    return bool(parts) and all(_key(p) in keys for p in parts)


async def search(driver, embedder, query: str, groups: list[str], limit: int,
                 min_score: float = MIN_SCORE) -> list[LedgerFact]:
    """The live facts in ``groups`` most relevant to ``query``, best first."""
    found, _ = await search_with_keys(driver, embedder, query, groups, limit, min_score)
    return found


async def search_with_keys(driver, embedder, query: str, groups: list[str], limit: int,
                           min_score: float = MIN_SCORE) -> tuple[list[LedgerFact], set[str]]:
    """``search``, plus the keys of every live fact in ``groups``."""
    records, _, _ = await driver.execute_query(_LIVE, groups=groups)
    if not records:
        return [], set()
    keys = {_key(r["statement"]) for r in records}
    q = await embedder.create(input_data=[QUERY_PROMPT + query])
    q_words = _words(query)
    scored = []
    for r in records:
        if not r["embedding"]:
            continue
        overlap = len(q_words & _words(r["statement"]))
        score = _cosine(q, r["embedding"]) + KEYWORD_BONUS * min(overlap, 2)
        if score >= min_score:
            created = r["created_at"]
            scored.append(LedgerFact(r["uuid"], r["group_id"], r["statement"], round(score, 4),
                                     created.to_native() if hasattr(created, "to_native") else created))
    scored.sort(key=lambda f: f.score, reverse=True)
    return scored[:limit], keys
