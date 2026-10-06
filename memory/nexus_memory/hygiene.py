"""Repairing a graph after the fact.

Some damage cannot be prevented at write time, because the contradiction has not
happened yet, and should not be repaired at read time, because you would pay for
it on every search and could not rebuild a node summary from a query anyway.
This is that repair, run as a pass over one group.

Measured on the 31 Aug run, which is what each step exists for:

- one node summary held the same sentence five times
- ``IS_GRAPH_STORE_FOR: Aarya's current graph store is Neo4j`` stayed live after
  the switch to Postgres, and outranked the correction in search
- ``IS_BUILDING: Aarya is building Nexus`` was expired despite still being true

Four rules the whole module is built around:

**Never cross a group boundary.** ``sweep`` takes one ``group_id``. Merging an
entity across the user tier and a project tier would destroy the isolation the
architecture rests on, and nobody would notice until a fact leaked between
projects.

**Mark, don't delete.** Superseded edges get ``expired_at`` and a
``nexus_superseded_by`` pointing at the survivor. This pass will be wrong
sometimes; marking is reversible and auditable, and it keeps the history that is
the point of a temporal graph. Orphan nodes are the sole exception, because a
node with nothing attached carries no information.

**Dry run by default.** The caller has to ask for changes.

**No model calls.** Near-duplicate detection rides on the ``fact_embedding``
graphiti already computed at write time; everything else is set logic. A
maintenance job full of model judgment is slow, non-reproducible and untestable.
Measure the residue this leaves before adding any.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# How close two facts on the same relation have to be before the older one is
# treated as superseded rather than as a second fact.
#
# Open: swept the way the retrieval floor was swept, not guessed. 0.85 is the
# starting point, not a measured answer.
NEAR_DUPLICATE_THRESHOLD = 0.85

# A node summary is quoted verbatim into the prompt, so it has to stay small
# enough not to crowd out the edges beside it.
SUMMARY_MAX_FACTS = 8
SUMMARY_MAX_CHARS = 600


# ── Locking ───────────────────────────────────────────────────────────────────
# ``add_episode`` runs 12-30s in a background task. A sweep mutating the same
# nodes concurrently corrupts a summary or loses an edge, and does it silently.
#
# This makes the single uvicorn worker load-bearing for a third reason, after
# the in-process ConnectionManager and BackgroundTasks. See start.sh.

_locks: dict[str, asyncio.Lock] = {}


def group_lock(group_id: str) -> asyncio.Lock:
    """One lock per group, created on first use."""
    lock = _locks.get(group_id)
    if lock is None:
        lock = _locks[group_id] = asyncio.Lock()
    return lock


# ── Values ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeRow:
    """One RELATES_TO, as the sweep needs it."""

    uuid: str
    source: str
    target: str
    name: str
    fact: str
    created_at: datetime | None = None
    valid_at: datetime | None = None
    expired_at: datetime | None = None
    invalid_at: datetime | None = None
    embedding: tuple[float, ...] | None = None

    @property
    def live(self) -> bool:
        return self.expired_at is None and self.invalid_at is None

    @property
    def when(self) -> datetime:
        """Best available ordering key. ``valid_at`` is when the fact became
        true; ``created_at`` is when we heard about it."""
        return self.valid_at or self.created_at or datetime.min.replace(tzinfo=timezone.utc)

    @property
    def triple(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.name)


@dataclass(frozen=True)
class Supersede:
    """One edge losing to another, with the reason a human would want."""

    loser: str
    winner: str
    note: str


@dataclass
class SweepReport:
    """What the pass found, and what it did about it.

    Counts alone are not enough: when this job is wrong you need to see which
    fact it touched, which is why every list holds text rather than a number.
    """

    group_id: str
    dry_run: bool = True
    duplicate_edges: list[str] = field(default_factory=list)
    near_duplicates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suspect_invalidations: list[str] = field(default_factory=list)
    summaries_rebuilt: list[str] = field(default_factory=list)
    episodes_suppressed: list[str] = field(default_factory=list)
    orphans_deleted: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Nothing to do. The target state after a run of the test script."""
        return not (
            self.duplicate_edges
            or self.near_duplicates
            or self.summaries_rebuilt
            or self.episodes_suppressed
            or self.orphans_deleted
        )

    @property
    def total_changes(self) -> int:
        return (
            len(self.duplicate_edges)
            + len(self.near_duplicates)
            + len(self.summaries_rebuilt)
            + len(self.episodes_suppressed)
            + len(self.orphans_deleted)
        )


# ── Pure planning ─────────────────────────────────────────────────────────────
# Everything below decides; nothing below touches Neo4j. That split is what
# makes the rules testable without a database.


def normalise(text: str) -> str:
    """Case, whitespace and a trailing full stop are not differences."""
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".")


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def plan_duplicate_collapse(edges: list[EdgeRow]) -> list[Supersede]:
    """Exact repeats: same triple, same sentence.

    The earliest wins, because it is the one the rest of the graph already
    points at and the one whose ``valid_at`` is honest about when the fact
    started being true.
    """
    buckets: dict[tuple[str, str, str, str], list[EdgeRow]] = defaultdict(list)
    for edge in edges:
        if edge.live:
            buckets[(*edge.triple, normalise(edge.fact))].append(edge)

    plans: list[Supersede] = []
    for group in buckets.values():
        if len(group) < 2:
            continue
        keeper, *losers = sorted(group, key=lambda e: (e.when, e.uuid))
        for loser in losers:
            plans.append(
                Supersede(loser.uuid, keeper.uuid, f"exact duplicate of {keeper.fact!r}")
            )
    return plans


def plan_near_duplicate_collapse(
    edges: list[EdgeRow], threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> tuple[list[Supersede], list[str]]:
    """Rephrasings of the same fact, by embedding. Newest wins.

    Clustering is confined to edges that share a relation *name*, which is the
    guardrail that matters. ``USES_AS_GRAPH_STORE`` and
    ``NO_LONGER_USES_GRAPH_STORE`` between the same two nodes are nearly
    identical to cosine and opposite in meaning; merging them would delete the
    correction and keep the stale fact. Differing names on the same pair come
    back as a conflict for a human instead.
    """
    by_triple: dict[tuple[str, str, str], list[EdgeRow]] = defaultdict(list)
    by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    for edge in edges:
        if not edge.live:
            continue
        by_triple[edge.triple].append(edge)
        by_pair[(edge.source, edge.target)].add(edge.name)

    plans: list[Supersede] = []
    for group in by_triple.values():
        usable = [e for e in group if e.embedding]
        if len(usable) < 2:
            continue
        # Newest first, so the survivor of each cluster is the current phrasing.
        ordered = sorted(usable, key=lambda e: (e.when, e.uuid), reverse=True)
        claimed: set[str] = set()
        for keeper in ordered:
            if keeper.uuid in claimed:
                continue
            for candidate in ordered:
                if candidate.uuid == keeper.uuid or candidate.uuid in claimed:
                    continue
                score = cosine(keeper.embedding, candidate.embedding)
                if score >= threshold:
                    claimed.add(candidate.uuid)
                    plans.append(
                        Supersede(
                            candidate.uuid,
                            keeper.uuid,
                            f"restates {keeper.fact!r} (cosine {score:.2f})",
                        )
                    )

    conflicts = [
        f"{sorted(names)} all live between the same pair — not merged, needs a human"
        for names in by_pair.values()
        if len(names) > 1
    ]
    return plans, conflicts


def find_suspect_invalidations(edges: list[EdgeRow]) -> list[str]:
    """Prose for the report. ``suspect_edge_uuids`` is the same rule, as ids."""
    return [note for _, note in _suspect_invalidations(edges)]


def suspect_edge_uuids(edges: list[EdgeRow]) -> set[str]:
    """Expired edges this pass believes are probably still true.

    Their facts stay in node summaries. Rebuilding a summary purely from live
    edges would mean trusting graphiti's invalidation, and we have measured it
    getting that wrong -- "Aarya is building Nexus" was expired while true, and
    a strict rebuild silently deleted it from every summary that held it.
    """
    return {uuid_ for uuid_, note in _suspect_invalidations(edges) if "still true" in note}


def _suspect_invalidations(edges: list[EdgeRow]) -> list[tuple[str, str]]:
    """Where graphiti's invalidation looks wrong. Reported, never auto-fixed.

    Two shapes, both seen on the 31 Aug run:

    - an expired edge with nothing live replacing it and nothing contradicting
      it, which is how "Aarya is building Nexus" died while still being true
    - a live edge older than an expired one on the same triple, meaning the
      invalidation landed on the wrong side
    """
    live_by_triple: dict[tuple[str, str, str], list[EdgeRow]] = defaultdict(list)
    for edge in edges:
        if edge.live:
            live_by_triple[edge.triple].append(edge)

    # A pair with any live edge between it has an ongoing story; a pair with
    # none has simply gone quiet, which is the suspicious case.
    live_pairs = {(e.source, e.target) for e in edges if e.live}

    notes: list[tuple[str, str]] = []
    for edge in edges:
        if edge.live:
            continue
        replacements = live_by_triple.get(edge.triple, [])
        if not replacements:
            if (edge.source, edge.target) not in live_pairs:
                notes.append(
                    (
                        edge.uuid,
                        f"{edge.fact!r} was expired with nothing replacing or "
                        f"contradicting it — possibly still true",
                    )
                )
            continue
        if all(r.when < edge.when for r in replacements):
            notes.append(
                (
                    edge.uuid,
                    f"{edge.fact!r} is expired but newer than the live edge "
                    f"that replaced it — invalidation looks backwards",
                )
            )
    return notes


def build_summary(facts: list[str]) -> str:
    """A node's summary, rebuilt from the facts of its live edges.

    Deliberately a list of true sentences rather than model-written prose.
    Retrieval quotes this verbatim into a prompt, so accuracy beats fluency,
    and a deterministic result is one you can assert on.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for fact in facts:
        key = normalise(fact)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(fact.strip())
        if len(kept) >= SUMMARY_MAX_FACTS:
            break

    summary = "\n".join(kept)
    if len(summary) > SUMMARY_MAX_CHARS:
        # Whole facts only. A truncated sentence in a prompt is worse than a
        # missing one, because the model will finish it.
        trimmed: list[str] = []
        budget = SUMMARY_MAX_CHARS
        for fact in kept:
            if len(fact) + 1 > budget:
                break
            trimmed.append(fact)
            budget -= len(fact) + 1
        summary = "\n".join(trimmed)
    return summary


def plan_episode_suppression(episodes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Episodes repeating one already in the group. Takes (uuid, content).

    Suppressed rather than deleted: they are the provenance record, and
    ``memory_episodes`` rows in Postgres point at them by uuid.
    """
    seen: dict[str, str] = {}
    suppress: list[tuple[str, str]] = []
    for uuid_, content in episodes:
        key = normalise(content)
        if not key:
            continue
        if key in seen:
            suppress.append((uuid_, content))
        else:
            seen[key] = uuid_
    return suppress


# ── Neo4j ─────────────────────────────────────────────────────────────────────
# Everything above decided. Everything below reads and writes.

_LOAD_EDGES = """
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
WHERE r.group_id = $group
RETURN r.uuid AS uuid, a.uuid AS source, b.uuid AS target,
       r.name AS name, r.fact AS fact,
       r.created_at AS created_at, r.valid_at AS valid_at,
       r.expired_at AS expired_at, r.invalid_at AS invalid_at,
       r.fact_embedding AS embedding
"""

_SUPERSEDE = """
UNWIND $plans AS plan
MATCH ()-[r:RELATES_TO {uuid: plan.loser}]->()
SET r.expired_at = $now,
    r.nexus_superseded_by = plan.winner
"""

_LOAD_NODES = """
MATCH (n:Entity)
WHERE n.group_id = $group
OPTIONAL MATCH (:Episodic)-[m:MENTIONS]->(n)
RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, count(m) AS mentions
"""

_WRITE_SUMMARIES = """
UNWIND $updates AS update
MATCH (n:Entity {uuid: update.uuid})
SET n.summary = update.summary
"""

_LOAD_EPISODES = """
MATCH (e:Episodic)
WHERE e.group_id = $group AND coalesce(e.nexus_suppressed, false) = false
RETURN e.uuid AS uuid, e.content AS content
ORDER BY e.created_at
"""

_SUPPRESS_EPISODES = """
UNWIND $uuids AS uuid
MATCH (e:Episodic {uuid: uuid})
SET e.nexus_suppressed = true
"""

_DELETE_ORPHANS = """
UNWIND $uuids AS uuid
MATCH (n:Entity {uuid: uuid})
DETACH DELETE n
"""


def _as_datetime(value) -> datetime | None:
    """Neo4j temporals come back as driver types, not datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    to_native = getattr(value, "to_native", None)
    return to_native() if to_native else None


async def _load_edges(driver, group_id: str) -> list[EdgeRow]:
    records, _, _ = await driver.execute_query(_LOAD_EDGES, group=group_id)
    return [
        EdgeRow(
            uuid=r["uuid"],
            source=r["source"],
            target=r["target"],
            name=r["name"] or "",
            fact=r["fact"] or "",
            created_at=_as_datetime(r["created_at"]),
            valid_at=_as_datetime(r["valid_at"]),
            expired_at=_as_datetime(r["expired_at"]),
            invalid_at=_as_datetime(r["invalid_at"]),
            embedding=tuple(r["embedding"]) if r["embedding"] else None,
        )
        for r in records
    ]


async def sweep(driver, group_id: str, *, dry_run: bool = True) -> SweepReport:
    """One maintenance pass over one group.

    Ordered, because the steps depend on each other: edges are settled first,
    and summaries are rebuilt last from whatever survived. Rebuilding summaries
    before collapsing duplicates would just write the duplicates into them.
    """
    report = SweepReport(group_id=group_id, dry_run=dry_run)
    now = datetime.now(timezone.utc)

    async with group_lock(group_id):
        edges = await _load_edges(driver, group_id)

        # B1 + B2 — settle which edges are current.
        duplicates = plan_duplicate_collapse(edges)
        collapsed = {p.loser for p in duplicates}
        survivors = [e for e in edges if e.uuid not in collapsed]

        near, conflicts = plan_near_duplicate_collapse(survivors)
        report.duplicate_edges = [f"{p.note} → superseded" for p in duplicates]
        report.near_duplicates = [f"{p.note} → superseded" for p in near]
        report.conflicts = conflicts

        plans = duplicates + near
        if plans and not dry_run:
            await driver.execute_query(
                _SUPERSEDE,
                plans=[{"loser": p.loser, "winner": p.winner} for p in plans],
                now=now,
            )

        # B3 — report only. This is where a model would be needed to judge, and
        # v1 makes no model calls.
        #
        # Run against the edges as graphiti left them, not against the state
        # after this pass. Our own supersessions are deliberate and would
        # otherwise be reported back to us as suspicious: an exact-duplicate
        # collapse keeps the *earliest* edge, which is exactly the "expired but
        # newer than its replacement" shape this looks for.
        superseded = collapsed | {p.loser for p in near}
        report.suspect_invalidations = find_suspect_invalidations(
            [e for e in edges if e.uuid not in superseded]
        )

        # B4 — rebuild summaries. Built here rather than in Cypher so the
        # suspect set can feed them: a fact this pass believes was wrongly
        # expired stays in the summary until a human resolves it.
        keep = suspect_edge_uuids([e for e in edges if e.uuid not in superseded])
        standing = [
            e
            for e in edges
            if e.uuid not in superseded and (e.live or e.uuid in keep)
        ]
        facts_by_node: dict[str, list[str]] = defaultdict(list)
        for e in sorted(standing, key=lambda e: e.when):
            facts_by_node[e.source].append(e.fact)
            facts_by_node[e.target].append(e.fact)

        node_records, _, _ = await driver.execute_query(_LOAD_NODES, group=group_id)
        updates: list[dict] = []
        orphans: list[str] = []
        for record in node_records:
            facts = [f for f in facts_by_node.get(record["uuid"], []) if f]
            existing = record["summary"] or ""
            if not facts and record["mentions"] == 0:
                # B6 — nothing standing and nothing mentions it. The only step
                # that deletes, because a true orphan carries no information.
                orphans.append(record["uuid"])
                report.orphans_deleted.append(record["name"])
                continue
            rebuilt = build_summary(facts)
            if not rebuilt and existing:
                # Mark, don't delete, applied to summaries. A node whose every
                # edge was expired still knows something; blanking it would
                # remove it from entity search entirely, and the invalidation
                # that emptied it is exactly what this pass distrusts.
                report.conflicts.append(
                    f"{record['name']!r} has no standing edges left; summary "
                    f"kept rather than blanked"
                )
                continue
            if rebuilt != existing:
                updates.append({"uuid": record["uuid"], "summary": rebuilt})
                report.summaries_rebuilt.append(record["name"])

        if updates and not dry_run:
            await driver.execute_query(_WRITE_SUMMARIES, updates=updates)
        if orphans and not dry_run:
            await driver.execute_query(_DELETE_ORPHANS, uuids=orphans)

        # B5 — duplicate episodes stop reaching search.
        episode_records, _, _ = await driver.execute_query(_LOAD_EPISODES, group=group_id)
        suppress = plan_episode_suppression(
            [(r["uuid"], r["content"] or "") for r in episode_records]
        )
        report.episodes_suppressed = [content[:90] for _, content in suppress]
        if suppress and not dry_run:
            uuids = [uuid_ for uuid_, _ in suppress]
            await driver.execute_query(_SUPPRESS_EPISODES, uuids=uuids)
            _suppressed.update(uuids)

    logger.info(
        "sweep group=%s dry_run=%s changes=%d clean=%s",
        group_id,
        dry_run,
        report.total_changes,
        report.clean,
    )
    return report


# ── Suppressed episodes ───────────────────────────────────────────────────────
# graphiti's ``EpisodicNode`` is a fixed Pydantic model, so a property we set on
# the node in Neo4j never reaches the search results. Rather than pay for a
# lookup on every search, the set is held in memory: loaded once at startup and
# extended by each sweep.
#
# In-process, so this joins the ConnectionManager and BackgroundTasks on the
# list of things that assume a single uvicorn worker.

_suppressed: set[str] = set()

_LOAD_SUPPRESSED = """
MATCH (e:Episodic)
WHERE e.nexus_suppressed = true
RETURN e.uuid AS uuid
"""


def is_suppressed(episode_uuid: str) -> bool:
    return episode_uuid in _suppressed


async def load_suppressed(driver) -> int:
    """Read the suppression set back after a restart."""
    records, _, _ = await driver.execute_query(_LOAD_SUPPRESSED)
    _suppressed.clear()
    _suppressed.update(r["uuid"] for r in records)
    return len(_suppressed)


# ── Retired facts ──────────────────────────────────────────────────────────
#
# A fact the person has since changed ("the deadline moved to the 21st") is
# retired: kept in Neo4j as history, never returned again. graphiti's own
# invalidation only covers relationship edges, and only when its extraction
# happens to notice the contradiction; a fact that lives in an entity's
# summary or an episode ("Demo User's project deadline is the 14th") has no
# such mechanism, so both versions kept coming back and the answer depended
# on the model choosing the newer one.
#
# Core's write gate decides what a correction replaces (it sees the facts
# memory holds, by id) and asks for those ids to be retired once the new fact
# is written. Same in-memory-set arrangement as suppression, for the same
# reason: it is consulted on every search.

_retired: set[str] = set()

# Every live item in some groups, with the text a search would return for it.
_LIVE_NODES = """
MATCH (n) WHERE n.group_id IN $groups AND (n:Entity OR n:Episodic) AND n.nexus_retired_at IS NULL
RETURN n.uuid AS uuid, 'Entity' IN labels(n) AS entity, coalesce(n.summary, n.content, '') AS text
"""

_LIVE_EDGES = """
MATCH ()-[e:RELATES_TO]->() WHERE e.group_id IN $groups AND e.nexus_retired_at IS NULL
RETURN e.uuid AS uuid, coalesce(e.fact, '') AS text
"""

_MARK_NODES = """
MATCH (n) WHERE n.uuid IN $ids SET n.nexus_retired_at = datetime()
"""

_MARK_EDGES = """
MATCH ()-[e:RELATES_TO]->() WHERE e.uuid IN $ids
SET e.nexus_retired_at = datetime(),
    e.invalid_at = coalesce(e.invalid_at, datetime()),
    e.expired_at = coalesce(e.expired_at, datetime())
"""

_LOAD_RETIRED = """
MATCH (n) WHERE n.nexus_retired_at IS NOT NULL RETURN n.uuid AS uuid
UNION
MATCH ()-[e:RELATES_TO]->() WHERE e.nexus_retired_at IS NOT NULL RETURN e.uuid AS uuid
"""

_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def _norm(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".")


def _one_statement(summary: str) -> bool:
    """An entity summary that says one thing. A summary that bundles several
    ("Demo User is 21. Demo User's father is Subhash.") is never retired:
    hiding the node would hide everything else it says."""
    return "\n" not in summary.strip() and len(_SENTENCE_END.findall(summary.strip())) <= 1


def is_retired(uuid_: str) -> bool:
    return uuid_ in _retired


async def retire(driver, texts: list[str], groups: list[str]) -> list[str]:
    """Retire every live item in ``groups`` that still says one of ``texts``.

    By text, not by id, for two reasons found on the live graph. The same
    sentence is often stored twice (an entity's summary and the episode it
    came from) and search shows only one of them, so retiring that one let
    the other back in. And graphiti may fold the new value into the old
    node, rewriting its summary: an id would then retire the new fact too,
    whereas its text no longer matches and it is left alone.
    """
    wanted = {_norm(t) for t in texts if t.strip()}
    if not wanted:
        return []
    nodes, _, _ = await driver.execute_query(_LIVE_NODES, groups=groups)
    edges, _, _ = await driver.execute_query(_LIVE_EDGES, groups=groups)
    node_ids = [r["uuid"] for r in nodes if _norm(r["text"]) in wanted
                and (not r["entity"] or _one_statement(r["text"]))]
    edge_ids = [r["uuid"] for r in edges if _norm(r["text"]) in wanted]
    if node_ids:
        await driver.execute_query(_MARK_NODES, ids=node_ids)
    if edge_ids:
        await driver.execute_query(_MARK_EDGES, ids=edge_ids)
    _retired.update(node_ids + edge_ids)
    return node_ids + edge_ids


async def load_retired(driver) -> int:
    """Read the retired set back after a restart."""
    records, _, _ = await driver.execute_query(_LOAD_RETIRED)
    _retired.clear()
    _retired.update(r["uuid"] for r in records)
    return len(_retired)
