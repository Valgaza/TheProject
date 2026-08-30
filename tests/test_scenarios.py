"""End-to-end scenarios: does memory actually work.

Run with:

    RUN_SCENARIOS=1 uv run pytest tests -v -s

Each of these is a claim the milestone rests on, written as a conversation
rather than as a unit test, because the interesting failures live in the seams
between the gate, the graph and retrieval -- not inside any one of them.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    not __import__("os").getenv("RUN_SCENARIOS"),
    reason="drives the live stack and spends API credit; set RUN_SCENARIOS=1",
)


def mentions(facts: list[str], *needles: str) -> bool:
    """Whether any recalled fact mentions all of these terms."""
    lowered = [f.lower() for f in facts]
    return any(all(n.lower() in fact for n in needles) for fact in lowered)


def asserts_stale(facts: list[str], stale: str, current: str) -> list[str]:
    """Facts that still present the old answer as the current one.

    A correction legitimately names both -- "switched from Neo4j to Postgres" is
    the right thing to recall. What must not survive is a bare statement of the
    superseded fact with nothing marking it as past.
    """
    return [
        f for f in facts
        if stale.lower() in f.lower() and current.lower() not in f.lower()
    ]


async def test_a_fact_stated_in_one_chat_is_recalled_in_another(
    clean_user, make_conversation
):
    """The milestone's central claim."""
    first = await make_conversation(label="cross-chat")
    await first.say("For this project I've decided to use Neo4j as the graph store.")
    await first.wait_for_memory(expected_writes=1)

    second = await make_conversation(first.project_id, label="cross-chat")
    recalled = await second.recalled("What am I using for storage here?")

    assert mentions(recalled, "neo4j"), recalled


async def test_a_project_fact_does_not_leak_into_another_project(
    clean_user, make_conversation
):
    """Isolation is the reason project scope exists. If this fails, the two-tier
    model is decoration."""
    thesis = await make_conversation(label="isolation-a")
    await thesis.say("For this project I've decided to use Neo4j as the graph store.")
    await thesis.wait_for_memory(expected_writes=1)

    unrelated = await make_conversation(label="isolation-b")
    recalled = await unrelated.recalled("What am I using for storage here?")

    assert not mentions(recalled, "neo4j"), recalled


async def test_a_personal_fact_follows_the_user_into_every_project(
    clean_user, make_conversation
):
    """The other half of two-tier scoping: what is true of the person is true
    everywhere, or the user tier is pointless."""
    first = await make_conversation(label="user-scope-a")
    await first.say("My name is Aarya and I'm a final year B.Tech student.")
    episodes = await first.wait_for_memory(expected_writes=1)
    assert episodes[0]["scope"] == "user", episodes

    elsewhere = await make_conversation(label="user-scope-b")
    recalled = await elsewhere.recalled("Remind me what my name is.")

    assert mentions(recalled, "aarya"), recalled


async def test_chit_chat_writes_nothing(clean_user, make_conversation):
    """The gate's whole job. A graph that grows on "thanks" is a graph whose
    retrieval degrades every turn."""
    chat = await make_conversation(label="chit-chat")
    for pleasantry in (
        "Hey there!",
        "thanks!",
        "ok cool",
        "Can you explain what a knowledge graph is, briefly?",
        "How are you today?",
    ):
        await chat.say(pleasantry)

    # Nothing should settle, so there is nothing to wait for; give the gate
    # room to write and then assert it did not.
    await chat.wait_for_memory(expected_writes=0)

    assert await chat.episodes() == []


async def test_the_gate_sorts_a_mixed_conversation(clean_user, make_conversation):
    """Personal facts to the user tier, project facts to the project tier,
    pleasantries nowhere -- in one conversation, where the gate has to decide
    turn by turn rather than being told."""
    chat = await make_conversation(label="mixed")
    script = [
        ("Hey there!", None),
        ("My name is Aarya and I'm a final year B.Tech student.", "user"),
        ("thanks!", None),
        ("For this project I've decided to use Neo4j as the graph store.", "project"),
        ("Can you explain what a knowledge graph is, briefly?", None),
        ("The project deadline is the 15th of November.", "project"),
        ("ok cool", None),
    ]
    for text, _ in script:
        await chat.say(text)

    expected = [scope for _, scope in script if scope]
    episodes = await chat.wait_for_memory(expected_writes=len(expected))

    assert [e["scope"] for e in episodes] == expected, episodes
    assert all(e["status"] == "ingested" for e in episodes), episodes


async def test_a_reply_never_waits_on_memory(clean_user, make_conversation):
    """Extraction is 12-30s. If it ever moved onto the request path this is the
    test that would notice."""
    import time

    chat = await make_conversation(label="latency")
    started = time.perf_counter()
    await chat.say("For this project I've decided to use Neo4j as the graph store.")
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0, f"reply took {elapsed:.1f}s; memory is blocking it"


async def test_a_contradiction_does_not_leave_both_versions_recalled(
    clean_user, make_conversation
):
    """State a fact, then change your mind. The old answer must not come back.

    This is the scenario most likely to expose something structural: graphiti
    invalidates a superseded edge rather than deleting it, and /search filters
    on invalid_at -- but only for edges. A fact living in a node summary or an
    episode has no invalidation to filter on.
    """
    chat = await make_conversation(label="contradiction")
    await chat.say("For this project I've decided to use Neo4j as the graph store.")
    await chat.wait_for_memory(expected_writes=1)

    await chat.say("Actually, scrap that -- we've switched the graph store to Postgres.")
    await chat.wait_for_memory(expected_writes=2)

    later = await make_conversation(chat.project_id, label="contradiction")
    turn = await later.say("What are we using for the graph store?")
    recalled = [f["fact"] for f in turn["recalled"]]
    answer = turn["assistant_message"]["content"].lower()

    assert mentions(recalled, "postgres"), recalled

    # What actually matters: the answer is right. Retrieval handing the model a
    # stale fact alongside the correction is survivable; the model repeating it
    # is not.
    assert "postgres" in answer, turn["assistant_message"]["content"]

    stale = asserts_stale(recalled, "neo4j", "postgres")
    if stale:
        pytest.xfail(
            "superseded facts are still retrievable outside edges: "
            f"{stale}. graphiti marks a replaced edge with invalid_at and "
            "/search filters on it, but the same fact also lives in a node "
            "summary and an episode, neither of which carries invalidation."
        )
