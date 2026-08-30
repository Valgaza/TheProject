"""Deciding what is worth remembering.

Two questions per turn, not one: is anything here worth keeping, and if so does
it belong to the person or to the project. The second question exists because
graphiti writes to exactly one group -- there is no writing to both and sorting
it out later.

Three deliberate choices:

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
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from .providers.base import Provider

logger = logging.getLogger(__name__)

Scope = Literal["user", "project"]

GATE_SYSTEM_PROMPT = """\
You decide what a personal assistant should remember from a conversation turn.

Return ONLY a JSON object, no prose and no code fence:
{"persist": bool, "scope": "user"|"project"|null, "statement": string|null, "reason": string}

Persist something only when it will still be useful weeks from now. Durable
facts qualify: who the person is, what they prefer, decisions they have made,
constraints they work under, and the shape of what they are building.

Persist nothing for: greetings, thanks, acknowledgements, questions that carry
no new information about the person, requests for explanation of general topics,
and anything the assistant said that is general knowledge rather than a fact
about this person or their work.

Scope:
- "user" for what stays true across everything they do: identity, background,
  preferences, working style, standing constraints.
- "project" for what is only true of the body of work in this conversation:
  technology choices, architecture decisions, deadlines, its specific problems.
When a fact would be equally true in a different project, it is "user".

statement: one self-contained sentence in the third person that stands on its
own with no conversation around it. Refer to the person by the name given below
and never as "the user", "they", or "the person" -- the sentence becomes a node
in a knowledge graph, and a graph full of facts about someone called "the user"
is a graph that cannot answer anything. Resolve every pronoun and every
reference the sentence would otherwise leave dangling. Null when persist is
false.

reason: a short phrase explaining the decision, for a human reading the logs.
"""


@dataclass(frozen=True)
class GateDecision:
    persist: bool
    reason: str
    scope: Scope | None = None
    statement: str | None = None

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

    if not data.get("persist"):
        return GateDecision.skip(str(data.get("reason", "gate declined")))

    scope = data.get("scope")
    statement = (data.get("statement") or "").strip()
    reason = str(data.get("reason", ""))

    # A persist decision missing either half is not a decision. Better to drop
    # it and say so than to guess a scope and quietly file a project fact into
    # somebody's durable memory.
    if scope not in ("user", "project"):
        return GateDecision.skip(f"gate said persist but gave scope {scope!r}")
    if not statement:
        return GateDecision.skip("gate said persist but gave no statement")

    return GateDecision(persist=True, scope=scope, statement=statement, reason=reason)


async def decide(
    provider: Provider,
    *,
    user_message: str,
    assistant_message: str,
    subject_name: str,
    model: str | None = None,
) -> GateDecision:
    """Whether this turn leaves anything behind, and where it goes.

    Never raises. A gate that fails means nothing is written, which is the safe
    direction: a missing memory is recoverable by saying the thing again, a
    wrong one has to be found first.
    """
    if looks_trivial(user_message):
        return GateDecision.skip("pre-filtered: message is a pleasantry")

    turn = (
        f"The person is named: {subject_name}\n\n"
        f"{subject_name}: {user_message}\n\n"
        f"Assistant: {assistant_message}"
    )
    try:
        raw = await provider.chat(
            [
                {"role": "system", "content": GATE_SYSTEM_PROMPT},
                {"role": "user", "content": turn},
            ],
            model=model,
        )
    except Exception as exc:
        logger.warning("Write gate call failed: %s", exc)
        return GateDecision.skip(f"gate call failed: {exc.__class__.__name__}")

    decision = _parse(raw)
    logger.info(
        "gate persist=%s scope=%s reason=%s statement=%r",
        decision.persist,
        decision.scope,
        decision.reason,
        (decision.statement or "")[:100],
    )
    return decision
