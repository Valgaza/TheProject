"""Deciding what is worth remembering.

Two questions per turn, not one: is anything here worth keeping, and if so does
it belong to the person or to the project. The second question exists because
graphiti writes to exactly one group -- there is no writing to both and sorting
it out later.

Five deliberate choices:

**Its own model call, never folded into the answer prompt.** The answer wants
the biggest model available; this wants the cheapest thing that can follow
instructions. Keeping them separate is what lets the gate move to a small local
model later while answering stays remote, which is the whole local-inference
plan in miniature.

**Never on the request path.** ``add_episode`` takes 12-30s (measured), so the
gate and the write it triggers run after the reply has already been sent. A user
waiting on their answer must never be waiting on memory.

**It stores a distilled statement, not the raw turn.** Feeding raw dialogue in
produced entities for the speaker label rather than the content. A self-contained
third-person sentence is what yields clean entities, and it is also what the
retrieval layer ends up quoting back into a prompt.

**Only the person's turn is a source of facts.** The assistant's message is
passed as context -- it resolves pronouns that "yes, and it also needs retries"
leaves dangling -- but nothing may be persisted on its authority alone. Without
this the gate learns from the model's own output: asking "what am I using for
the graph store?" got the answer "Neo4j", and the gate wrote that answer back as
a new fact. Measured on the 31 Aug run: seven episodes for four distinct facts,
with one node summary holding the same sentence five times.

**Facts already in memory are named in the prompt and refused.** Same failure,
second layer. Recall makes a fact louder every time it is recalled, so the
recalled set for this turn is listed as already known.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from .providers.base import Provider

logger = logging.getLogger(__name__)

Scope = Literal["user", "project"]

# One turn can carry several durable facts -- "my name is Aarya and I'm using
# Neo4j for this" is one message with a user-tier fact and a project-tier one.
# It cannot carry many. Without a cap a long message becomes ten episodes at
# 12-30s of ingest each, and the graph fills with restatements of one paragraph.
MAX_FACTS_PER_TURN = 3

# How much overlap counts as "you already knew this". Measured against the
# duplicates the 31 Aug run produced, which were near-verbatim: "Aarya uses
# Neo4j as the graph store" against "Aarya is using Neo4j as the graph store
# for this project" scores 0.71, and the true repeats score 1.0.
DUPLICATE_OVERLAP = 0.8

# The same shape the prompt describes, enforced. Served by the MLX runtime,
# decoding is constrained to it (xgrammar), so even a small model cannot
# return prose, a code fence, or a scope that is neither tier. _parse still
# validates: a server that ignores response_format must not break the gate.
GATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "persist": {"type": "boolean"},
        "facts": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "scope": {"enum": ["user", "project"]},
                },
                "required": ["statement", "scope"],
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["persist", "facts", "reason"],
}

GATE_SYSTEM_PROMPT = """\
You decide what a personal assistant should remember from a conversation turn.

Return ONLY a JSON object, no prose and no code fence:
{"persist": bool, "facts": [{"statement": string, "scope": "user"|"project"}], "reason": string}

ONLY the person's own message is a source of facts. The assistant's reply is
given to you as context, so you can resolve references the person's message
leaves dangling -- never as something to learn from. If the only new information
in this turn came out of the assistant's mouth, persist nothing. An assistant
repeating back a fact it was told earlier is not the person stating it again.

Facts already in memory are listed below when there are any. Never restate one.
A rephrasing of something already known is a duplicate, not a new fact.

Persist something only when it will still be useful weeks from now. Durable
facts qualify: who the person is, what they prefer, decisions they have made,
constraints they work under, and the shape of what they are building.

Persist nothing for: greetings, thanks, acknowledgements, questions that carry
no new information about the person, requests for explanation of general topics,
and anything the assistant said that is general knowledge rather than a fact
about this person or their work.

facts: a list, because one message can carry more than one durable fact. "My
name is Ada and I'm using Rust for this" is two facts in two different scopes.
Emit one entry per distinct fact and never more than three. Empty list when
persist is false.

statement: one self-contained sentence in the third person that stands on its
own with no conversation around it. Refer to the person by the name given below
and never as "the user", "they", or "the person" -- the sentence becomes a node
in a knowledge graph, and a graph full of facts about someone called "the user"
is a graph that cannot answer anything. Resolve every pronoun and every
reference the sentence would otherwise leave dangling.

scope, set per fact:
- "user" for what stays true across everything they do: identity, background,
  preferences, working style, standing constraints.
- "project" for what is only true of the body of work in this conversation:
  technology choices, architecture decisions, deadlines, its specific problems.
When a fact would be equally true in a different project, it is "user".

reason: a short phrase explaining the decision, for a human reading the logs.
"""


@dataclass(frozen=True)
class GateFact:
    """One thing worth remembering, and which tier it belongs to.

    Scope sits on the fact rather than the decision because a single turn can
    produce facts for different tiers, and graphiti writes each episode to
    exactly one group.
    """

    statement: str
    scope: Scope


@dataclass(frozen=True)
class GateDecision:
    persist: bool
    reason: str
    facts: tuple[GateFact, ...] = ()

    @classmethod
    def skip(cls, reason: str) -> "GateDecision":
        return cls(persist=False, reason=reason)


# Whole-message pleasantries. Deliberately an exact-match list rather than a
# length cutoff: "I use Neo4j" is eleven characters and matters, while "thanks
# so much for that!" is long and does not.
_TRIVIAL = {
    "hi", "hey", "hello", "yo", "sup",
    "thanks", "thank you", "ty", "thx", "cheers", "much appreciated",
    "ok", "okay", "k", "cool", "nice", "great", "perfect", "got it", "sounds good",
    "yes", "no", "yep", "nope", "sure", "please", "bye", "goodbye", "see you",
}


def looks_trivial(text: str) -> bool:
    """A cheap pre-filter, to avoid spending a model call on "thanks".

    Conservative on purpose. It only fires when the entire message is a
    pleasantry, because the cost of wrongly skipping a real fact is a memory
    that silently never forms, which is far worse than one wasted call.
    """
    stripped = re.sub(r"[^a-z\s]", "", text.strip().lower()).strip()
    return stripped in _TRIVIAL


_WORD = re.compile(r"[a-z0-9]+")

# Words carried by almost every statement the gate writes, and therefore useless
# for telling two of them apart. Without this, "Aarya is a final year B.Tech
# student" and "Aarya is building Nexus" share enough of "aarya is a" to drift
# toward the threshold.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "for", "of", "to", "in", "on", "at", "as", "by", "with", "this", "that",
        "these", "those", "it", "its", "and", "or", "has", "have", "had",
        "currently", "now", "uses", "using", "use",
    }
)


def _content_words(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(text.lower())) - _STOPWORDS


def overlap(left: str, right: str) -> float:
    """Jaccard overlap of content words. 1.0 is the same sentence.

    Lexical rather than embedding-based on purpose. An embedding check would
    mean an extra API call per turn on the write path, and the duplicates this
    exists to catch are near-verbatim -- the prompt is the layer that catches
    genuine rephrasings.
    """
    a, b = _content_words(left), _content_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def drop_known(
    facts: Sequence[GateFact], known: Sequence[str]
) -> tuple[tuple[GateFact, ...], list[str]]:
    """Remove statements that repeat something memory already supplied.

    The backstop behind the prompt. Returns what survives, and what was dropped
    so the caller can log it -- a silently discarded fact is indistinguishable
    from a gate that never fired.
    """
    kept: list[GateFact] = []
    dropped: list[str] = []
    for fact in facts:
        match = next(
            (k for k in known if overlap(fact.statement, k) >= DUPLICATE_OVERLAP),
            None,
        )
        if match is None:
            kept.append(fact)
        else:
            dropped.append(f"{fact.statement!r} ≈ {match!r}")
    return tuple(kept), dropped


def _parse(raw: str) -> GateDecision:
    """Read the gate's JSON, tolerating a code fence around it."""
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return GateDecision.skip(f"gate returned unparseable output: {raw[:120]!r}")

    reason = str(data.get("reason", ""))
    if not data.get("persist"):
        return GateDecision.skip(reason or "gate declined")

    entries = data.get("facts")
    if not isinstance(entries, list):
        return GateDecision.skip("gate said persist but gave no facts list")

    facts: list[GateFact] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        statement = str(entry.get("statement") or "").strip()
        scope = entry.get("scope")
        # A fact missing either half is not a fact. Dropped individually rather
        # than failing the whole turn: one malformed entry should not lose the
        # good ones beside it.
        if not statement:
            logger.warning("gate emitted a fact with no statement; dropped")
            continue
        if scope not in ("user", "project"):
            logger.warning(
                "gate emitted scope %r for %r; dropped rather than guessed",
                scope,
                statement[:60],
            )
            continue
        facts.append(GateFact(statement=statement, scope=scope))

    if not facts:
        return GateDecision.skip(f"gate said persist but gave no usable facts ({reason})")

    if len(facts) > MAX_FACTS_PER_TURN:
        logger.info(
            "gate emitted %d facts; keeping the first %d",
            len(facts),
            MAX_FACTS_PER_TURN,
        )
        facts = facts[:MAX_FACTS_PER_TURN]

    return GateDecision(persist=True, reason=reason, facts=tuple(facts))


async def decide(
    provider: Provider,
    *,
    user_message: str,
    assistant_message: str,
    subject_name: str,
    known_facts: Sequence[str] = (),
    model: str | None = None,
) -> GateDecision:
    """What this turn leaves behind, and where each piece of it goes.

    Never raises. A gate that fails means nothing is written, which is the safe
    direction: a missing memory is recoverable by saying the thing again, a
    wrong one has to be found first.
    """
    if looks_trivial(user_message):
        return GateDecision.skip("pre-filtered: message is a pleasantry")

    sections = [f"The person is named: {subject_name}"]
    if known_facts:
        already = "\n".join(f"- {f}" for f in known_facts)
        sections.append(f"Already in memory, do not restate:\n{already}")
    sections.append(f"{subject_name} said:\n{user_message}")
    sections.append(f"The assistant replied (context only, not a source):\n{assistant_message}")

    try:
        raw = await provider.chat(
            [
                {"role": "system", "content": GATE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(sections)},
            ],
            model=model,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "memory_gate", "schema": GATE_SCHEMA},
            },
        )
    except Exception as exc:
        logger.warning("Write gate call failed: %s", exc)
        return GateDecision.skip(f"gate call failed: {exc.__class__.__name__}")

    decision = _parse(raw)

    if decision.persist and known_facts:
        surviving, dropped = drop_known(decision.facts, known_facts)
        for note in dropped:
            logger.info("gate restated a recalled fact; dropped  %s", note)
        if not surviving:
            return GateDecision.skip(
                f"every proposed fact was already in memory ({decision.reason})"
            )
        decision = GateDecision(
            persist=True, reason=decision.reason, facts=surviving
        )

    logger.info(
        "gate persist=%s reason=%s facts=%s",
        decision.persist,
        decision.reason,
        [(f.scope, f.statement[:70]) for f in decision.facts],
    )
    return decision
