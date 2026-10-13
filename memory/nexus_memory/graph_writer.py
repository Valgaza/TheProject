"""Writing the graph straight from the gate's facts, without a second LLM pass.

The write gate already turns a message into clean facts, and now gives each
one as a triple: who or what it is about, the relationship, and the other end
("Kavya" IS_SISTER_OF "Meera"). Graphiti's own route re-reads the text with
an LLM to find the same things; with the 8B model that pass found 4
relationships in 22 facts in a scripted run (2026-10-13), split one person
into "Meera" and "Meera Iyer", and took 12-30 s per write.

So each triple is written as graphiti's own node and edge models, in code:

- An entity is looked up in the tier before one is made: the same name
  (case, punctuation and a trailing "'s" aside), a shorter or longer form of
  it ("Meera" / "Meera Iyer", "Postgres" / "PostgreSQL"), or a name whose
  embedding is nearly identical. Otherwise it is created.
- The relationship is an edge whose text is the fact itself, so retirement
  (hygiene.retire matches facts by text) and search work as they always have.
- Each entity's summary is its facts, listed, as the sweep already builds it.

No model is asked anything, so the result is the same every time and a
write takes milliseconds. Graphiti's extraction is still used when a write
comes without triples (a retry of an older turn).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from graphiti_core.edges import EntityEdge
from graphiti_core.errors import GroupsEdgesNotFoundError
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

from .hygiene import build_summary
from .ledger import _cosine

logger = logging.getLogger(__name__)

SAME_NAME = 0.92  # name-embedding similarity at which two names are one entity


@dataclass(frozen=True)
class Triple:
    statement: str
    subject: str
    relation: str
    object: str


def norm_name(name: str) -> str:
    name = re.sub(r"[’']s\b", "", name.strip().lower())
    name = re.sub(r"^(the|a|an)\s+", "", name)
    return " ".join(re.findall(r"[a-z0-9]+", name))


def _variant(a: str, b: str) -> bool:
    """One name is a shorter form of the other: same first word, and every
    word of the shorter is in the longer ("meera" / "meera iyer"), or one is
    the other plus a suffix ("postgres" / "postgresql"). Compared on the raw
    names: a possessive ("GreenLoop's beta") is a part of something, not
    another name for it, and once took GreenLoop over."""
    if re.search(r"[’']s\b", a) or re.search(r"[’']s\b", b):
        return False
    a, b = norm_name(a), norm_name(b)
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if ta[0] == tb[0] and (set(ta) <= set(tb) or set(tb) <= set(ta)):
        return True
    return len(ta) == len(tb) == 1 and min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))


def relation_name(relation: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", relation)
    return "_".join(w.upper() for w in words)[:60] or "RELATES_TO"


async def _entities(driver, group_id: str) -> list[EntityNode]:
    try:
        # With embeddings: a node whose summary changes is saved back whole,
        # and saving one loaded without its embedding would erase it.
        return await EntityNode.get_by_group_ids(driver, [group_id], with_embeddings=True)
    except Exception:
        return []


async def _edges(driver, group_id: str) -> list[EntityEdge]:
    try:
        return await EntityEdge.get_by_group_ids(driver, [group_id])
    except GroupsEdgesNotFoundError:
        return []


async def write(graphiti, group_id: str, text: str, triples: list[Triple], occurred_at: datetime | None = None) -> str:
    """Write the episode and its triples into ``group_id``. Returns the
    episode's uuid. ``occurred_at`` dates history written after the fact."""
    driver, embedder = graphiti.driver, graphiti.embedder
    now = datetime.now(timezone.utc)
    happened = occurred_at or now
    episode = EpisodicNode(
        name="episode", group_id=group_id, source=EpisodeType.text,
        source_description=f"nexus statement ({group_id})", content=text,
        valid_at=happened, created_at=now,
    )

    entities = await _entities(driver, group_id)
    by_norm = {norm_name(e.name): e for e in entities}

    def owned_thing(name: str) -> str | None:
        """"Meera's thesis", when Meera is an entity here: the thesis. One
        run made "The thesis" and "Meera's thesis" two entities."""
        m = re.match(r"^\s*(.+?)[’']s\s+(.+)$", name)
        if m and norm_name(m[1]) in by_norm or (m and any(_variant(m[1], n.name) for n in by_norm.values())):
            return norm_name(m[2])
        return None

    for e in entities:
        thing = owned_thing(e.name)
        if thing and thing not in by_norm:
            by_norm[thing] = e
    edges = await _edges(driver, group_id)
    known_facts = {" ".join(e.fact.lower().split()) for e in edges}
    touched: dict[str, EntityNode] = {}

    async def resolve(name: str) -> EntityNode:
        key = norm_name(name)
        if key in by_norm:
            return by_norm[key]
        thing = owned_thing(name)
        if thing and thing in by_norm:
            by_norm[key] = by_norm[thing]
            return by_norm[thing]
        for existing_key, node in list(by_norm.items()):
            if _variant(name, node.name):
                if len(name) > len(node.name) and set(existing_key.split()) <= set(key.split()):
                    node.name = name.strip()  # keep the fuller form: "Meera Iyer"
                    await node.generate_name_embedding(embedder)
                    touched[node.uuid] = node
                by_norm[key] = node
                return node
        vector = await embedder.create(input_data=[name.strip()])
        for node in list(by_norm.values()):
            if node.name_embedding and _cosine(vector, node.name_embedding) >= SAME_NAME:
                by_norm[key] = node
                return node
        node = EntityNode(name=name.strip(), group_id=group_id, labels=["Entity"], summary="",
                          name_embedding=list(vector), created_at=now)
        by_norm[key] = node
        if thing and thing not in by_norm:
            by_norm[thing] = node
        touched[node.uuid] = node
        return node

    new_edges: list[EntityEdge] = []
    for t in triples:
        if not (t.subject.strip() and t.object.strip() and t.statement.strip()):
            continue
        fact_key = " ".join(t.statement.lower().split())
        if fact_key in known_facts:
            continue
        source, target = await resolve(t.subject), await resolve(t.object)
        if source.uuid == target.uuid:
            continue
        edge = EntityEdge(group_id=group_id, source_node_uuid=source.uuid, target_node_uuid=target.uuid,
                          created_at=now, name=relation_name(t.relation), fact=t.statement.strip(),
                          episodes=[episode.uuid], valid_at=happened if occurred_at else None)
        await edge.generate_embedding(embedder)
        new_edges.append(edge)
        known_facts.add(fact_key)
        touched[source.uuid], touched[target.uuid] = source, target

    # Summaries from every live fact touching each node, new and old.
    facts_of: dict[str, list[str]] = {}
    for e in [*edges, *new_edges]:
        if e.expired_at is None and e.invalid_at is None:
            for uid in (e.source_node_uuid, e.target_node_uuid):
                facts_of.setdefault(uid, []).append(e.fact)
    for node in touched.values():
        node.summary = build_summary(facts_of.get(node.uuid, []))
        await node.save(driver)
    episode.entity_edges = [e.uuid for e in new_edges]
    await episode.save(driver)
    for edge in new_edges:
        await edge.save(driver)
    logger.info("graph written for group=%s: %d triples, %d new edges, %d entities touched",
                group_id, len(triples), len(new_edges), len(touched))
    return episode.uuid
