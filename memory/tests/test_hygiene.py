"""The maintenance pass, decided without a database.

Every rule here is a pure function on purpose: the sweep's job is to decide
which facts survive, and that decision should be assertable without Neo4j
running. The Cypher underneath is thin by comparison.

The fixtures are the real damage from the 31 Aug run, not invented shapes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nexus_memory.hygiene import (
    EdgeRow,
    SweepReport,
    build_summary,
    cosine,
    find_suspect_invalidations,
    group_lock,
    normalise,
    plan_duplicate_collapse,
    plan_episode_suppression,
    plan_near_duplicate_collapse,
    suspect_edge_uuids,
)

T0 = datetime(2026, 8, 31, 11, 58, tzinfo=timezone.utc)
AARYA, NEO4J, NEXUS, POSTGRES = "n-aarya", "n-neo4j", "n-nexus", "n-postgres"


def edge(uuid, source, target, name, fact, *, minutes=0, expired=False, embedding=None):
    return EdgeRow(
        uuid=uuid,
        source=source,
        target=target,
        name=name,
        fact=fact,
        created_at=T0 + timedelta(minutes=minutes),
        valid_at=T0 + timedelta(minutes=minutes),
        expired_at=T0 + timedelta(hours=1) if expired else None,
        embedding=embedding,
    )


# ── Normalising ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "left,right",
    [
        ("Aarya uses Neo4j.", "aarya uses neo4j"),
        ("Aarya  uses   Neo4j", "Aarya uses Neo4j."),
        (" Aarya uses Neo4j ", "AARYA USES NEO4J."),
    ],
)
def test_case_spacing_and_a_full_stop_are_not_differences(left, right):
    assert normalise(left) == normalise(right)


# ── B1: exact duplicates ──────────────────────────────────────────────────────


def test_the_verbatim_repeat_is_collapsed():
    """The 31 Aug graph held this sentence twice on the same triple."""
    fact = "Aarya's current graph store for the project is Neo4j."
    edges = [
        edge("e1", AARYA, NEO4J, "IS_GRAPH_STORE_FOR", fact, minutes=0),
        edge("e2", AARYA, NEO4J, "IS_GRAPH_STORE_FOR", fact, minutes=5),
    ]

    plans = plan_duplicate_collapse(edges)

    assert len(plans) == 1
    # The earliest survives: the rest of the graph already points at it and its
    # valid_at is honest about when the fact started being true.
    assert plans[0].loser == "e2"
    assert plans[0].winner == "e1"


def test_the_same_sentence_between_different_nodes_is_not_a_duplicate():
    """Two projects both using Postgres is two facts, not one."""
    edges = [
        edge("e1", AARYA, POSTGRES, "USES", "Aarya uses Postgres."),
        edge("e2", NEXUS, POSTGRES, "USES", "Aarya uses Postgres."),
    ]

    assert plan_duplicate_collapse(edges) == []


def test_an_already_expired_edge_is_left_alone():
    fact = "Aarya uses Neo4j."
    edges = [
        edge("e1", AARYA, NEO4J, "USES", fact, expired=True),
        edge("e2", AARYA, NEO4J, "USES", fact),
    ]

    assert plan_duplicate_collapse(edges) == []


# ── B2: near duplicates ───────────────────────────────────────────────────────


def test_a_rephrasing_of_the_same_fact_is_collapsed_newest_first():
    edges = [
        edge("old", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", minutes=0, embedding=(1.0, 0.0)),
        edge("new", AARYA, NEO4J, "USES", "Aarya is using Neo4j for this.", minutes=5, embedding=(0.99, 0.14)),
    ]

    plans, _ = plan_near_duplicate_collapse(edges)

    assert [p.loser for p in plans] == ["old"]
    assert plans[0].winner == "new"


def test_a_correction_is_never_merged_into_the_fact_it_corrects():
    """The guardrail that matters most.

    "uses Neo4j" and "no longer uses Neo4j" sit between the same two nodes and
    are nearly identical to cosine. Merging them would delete the correction and
    keep the stale fact, which is the exact failure this pass exists to fix.
    """
    edges = [
        edge("pos", AARYA, NEO4J, "USES_AS_GRAPH_STORE", "Aarya uses Neo4j as the graph store.", minutes=0, embedding=(1.0, 0.0)),
        edge("neg", AARYA, NEO4J, "NO_LONGER_USES_GRAPH_STORE", "Aarya no longer uses Neo4j as the graph store.", minutes=5, embedding=(1.0, 0.0)),
    ]

    plans, conflicts = plan_near_duplicate_collapse(edges)

    assert plans == []
    assert len(conflicts) == 1
    assert "USES_AS_GRAPH_STORE" in conflicts[0]


def test_unrelated_facts_on_one_relation_both_survive():
    edges = [
        edge("e1", AARYA, NEXUS, "WORKS_ON", "Aarya works on Nexus.", embedding=(1.0, 0.0)),
        edge("e2", AARYA, NEXUS, "WORKS_ON", "Aarya started Nexus in June.", embedding=(0.0, 1.0)),
    ]

    plans, _ = plan_near_duplicate_collapse(edges)

    assert plans == []


def test_an_edge_with_no_embedding_is_skipped_rather_than_guessed():
    edges = [
        edge("e1", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", embedding=(1.0, 0.0)),
        edge("e2", AARYA, NEO4J, "USES", "Aarya uses Neo4j!", embedding=None),
    ]

    plans, _ = plan_near_duplicate_collapse(edges)

    assert plans == []


def test_cosine_handles_a_zero_vector_without_dividing_by_zero():
    assert cosine((0.0, 0.0), (1.0, 0.0)) == 0.0


# ── B3: suspect invalidations ─────────────────────────────────────────────────


def test_a_fact_expired_with_nothing_replacing_it_is_flagged():
    """graphiti expired "Aarya is building Nexus" while it was still true."""
    edges = [
        edge("e1", AARYA, NEXUS, "IS_BUILDING", "Aarya is building Nexus.", expired=True),
    ]

    notes = find_suspect_invalidations(edges)

    assert len(notes) == 1
    assert "possibly still true" in notes[0]


def test_a_properly_superseded_fact_is_not_flagged():
    edges = [
        edge("old", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", minutes=0, expired=True),
        edge("new", AARYA, NEO4J, "USES", "Aarya uses Postgres.", minutes=10),
    ]

    assert find_suspect_invalidations(edges) == []


def test_an_expired_edge_newer_than_its_replacement_is_flagged():
    edges = [
        edge("live", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", minutes=0),
        edge("dead", AARYA, NEO4J, "USES", "Aarya uses Neo4j daily.", minutes=30, expired=True),
    ]

    notes = find_suspect_invalidations(edges)

    assert len(notes) == 1
    assert "backwards" in notes[0]


def test_a_contradiction_on_another_relation_counts_as_an_explanation():
    """A pair with a live edge between it has an ongoing story, so an expired
    edge there is a normal correction rather than a suspicious disappearance."""
    edges = [
        edge("dead", AARYA, NEO4J, "USES_AS_GRAPH_STORE", "Aarya uses Neo4j.", expired=True),
        edge("live", AARYA, NEO4J, "NO_LONGER_USES", "Aarya stopped using Neo4j.", minutes=10),
    ]

    assert find_suspect_invalidations(edges) == []


# ── B4: summaries ─────────────────────────────────────────────────────────────


def test_the_summary_is_rebuilt_without_repeats():
    """The measured symptom: one fact, five times, in one node's summary."""
    facts = [
        "Nexus uses Neo4j as its graph store.",
        "Aarya uses Neo4j as the graph store.",
        "Aarya's current graph store for the project is Neo4j.",
        "Aarya's current graph store for the project is Neo4j.",
        "Aarya is using Neo4j as the graph store for this project.",
    ]

    summary = build_summary(facts)

    assert summary.count("Aarya's current graph store") == 1
    assert len(summary.splitlines()) == 4


def test_the_summary_is_capped_on_whole_facts():
    """A truncated sentence in a prompt is worse than a missing one, because
    the model will finish it."""
    facts = [f"Aarya knows a fact about topic number {i} in some detail." * 3 for i in range(20)]

    summary = build_summary(facts)

    assert len(summary) <= 600
    assert all(line.endswith(".") for line in summary.splitlines())


def test_an_empty_fact_list_gives_an_empty_summary():
    assert build_summary([]) == ""


# ── B5: episodes ──────────────────────────────────────────────────────────────


def test_a_repeated_episode_is_suppressed_and_the_first_is_kept():
    episodes = [
        ("ep1", "Aarya uses Neo4j as the graph store."),
        ("ep2", "Aarya is a final year B.Tech student."),
        ("ep3", "aarya uses neo4j as the graph store"),
    ]

    suppress = plan_episode_suppression(episodes)

    assert [uuid_ for uuid_, _ in suppress] == ["ep3"]


# ── The report ────────────────────────────────────────────────────────────────


def test_a_report_with_nothing_in_it_is_clean():
    """The testing goal: after a scripted run, a dry sweep changes nothing."""
    assert SweepReport(group_id="u_x").clean


def test_conflicts_alone_do_not_make_a_report_dirty():
    """A conflict is something for a human to look at, not something the pass
    would have changed."""
    report = SweepReport(group_id="u_x", conflicts=["two relations between one pair"])

    assert report.clean
    assert report.total_changes == 0


def test_the_lock_is_per_group_and_stable():
    """Ingest and sweep have to contend on the same object, or the lock does
    nothing at all."""
    assert group_lock("u_x") is group_lock("u_x")
    assert group_lock("u_x") is not group_lock("p_y")


# ── The rebuild must not trust a bad invalidation ─────────────────────────────
# Rebuilding a summary purely from live edges means believing graphiti's
# invalidation, and we measured it expiring "Aarya is building Nexus" while it
# was still true. A strict rebuild deleted that fact from every summary holding
# it and left two nodes with nothing at all.


def test_a_wrongly_expired_fact_stays_in_the_summary():
    edges = [
        edge("e1", AARYA, NEXUS, "IS_BUILDING", "Aarya is building Nexus.", expired=True),
    ]

    assert suspect_edge_uuids(edges) == {"e1"}


def test_a_properly_superseded_fact_does_not_stay_in_the_summary():
    edges = [
        edge("old", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", minutes=0, expired=True),
        edge("new", AARYA, NEO4J, "USES", "Aarya uses Postgres.", minutes=10),
    ]

    assert suspect_edge_uuids(edges) == set()


def test_a_backwards_invalidation_is_reported_but_not_kept():
    """Two shapes, two responses. "Expired with nothing replacing it" probably
    means still true. "Expired but newer than its replacement" means the
    ordering is wrong, and guessing which side to believe needs a human."""
    edges = [
        edge("live", AARYA, NEO4J, "USES", "Aarya uses Neo4j.", minutes=0),
        edge("dead", AARYA, NEO4J, "USES", "Aarya uses Neo4j daily.", minutes=30, expired=True),
    ]

    assert find_suspect_invalidations(edges) != []
    assert suspect_edge_uuids(edges) == set()
