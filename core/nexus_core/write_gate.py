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
from datetime import datetime
from dataclasses import dataclass
from typing import Literal, Sequence

from .clock import describe_now
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
# "reason" first: with constrained decoding the keys are generated in this
# order, so the model states why before it commits to persist. With persist
# first, Qwen3-8B declined a project decision and then explained it as "not
# a durable fact about the user" (scenario suite, 2026-10-04).
GATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "persist": {"type": "boolean"},
        "facts": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "scope": {"enum": ["user", "project"]},
                    "subject": {"type": "string"},
                    "relation": {"type": "string"},
                    "object": {"type": "string"},
                },
                "required": ["statement", "scope", "subject", "relation", "object"],
            },
        },
        "replaces": {"type": "array", "maxItems": 6, "items": {"type": "integer"}},
    },
    "required": ["reason", "persist", "facts", "replaces"],
}

GATE_SYSTEM_PROMPT = """\
You decide what a personal assistant should remember from a conversation turn.

Return ONLY a JSON object, no prose and no code fence:
{"reason": string, "persist": bool,
 "facts": [{"statement": string, "scope": "user"|"project", "subject": string, "relation": string, "object": string}],
 "replaces": [number]}

ONLY the person's own message is a source of facts. The assistant's reply is
given to you as context, so you can resolve references the person's message
leaves dangling -- never as something to learn from. If the only new information
in this turn came out of the assistant's mouth, persist nothing. An assistant
repeating back a fact it was told earlier is not the person stating it again.

Facts already in memory are listed below, numbered, when there are any. Never
restate one. A rephrasing of something already known is a duplicate, not a new
fact. A change of mind is not a duplicate: when the person replaces, reverses
or corrects something already known ("scrap that, we switched to Postgres"),
persist the new state as a fact, saying what it replaced.

Persist something only when it will still be useful weeks from now. Durable
facts qualify: who the person is, what they prefer, decisions they have made,
constraints they work under, and the shape of what they are building. Facts
about their project count exactly as much as facts about them; they go in the
project scope, below.

Persist nothing for: greetings, thanks, acknowledgements, questions that carry
no new information about the person, requests for explanation of general topics,
and anything the assistant said that is general knowledge rather than a fact
about this person or their work. A question about a document ("who climbed
them?") is not a fact about the person, even when the reply answers it.
Hypotheticals, wishes and maybes ("if I had more time I'd learn Rust", "I
might try Go someday") are not facts either.

Never carry a detail from the facts already in memory, or from the reply, into
a new fact. Each statement holds only what this message says: "I'm presenting
it to the department" is "presenting to the department", with no date, even if
a date appears in memory. Don't record the person's own name; it is known.

facts: a list, because one message can carry more than one durable fact. "My
name is Ada and I'm using Rust for this" is two facts in two different scopes.
Emit one entry per distinct fact, up to five. Don't drop any: every person
named, their relationship to the person and what they do is its own fact.
"My co-founder Arjun handles sales and I handle the tech" is three: Arjun is
the person's co-founder; Arjun handles sales; the person handles the tech. "My
sister Kavya is a doctor" keeps "sister": Kavya is the person's sister; Kavya
is a doctor. Empty list when persist is false.

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
  technology choices, architecture decisions, deadlines, its specific problems,
  and who people are on it (a co-founder, supervisor, client or teammate is
  someone's role in this work, so "project").
When a fact would be equally true in a different project, it is "user".

replaces: the numbers of the facts already in memory that this message makes
untrue -- the old value of something the person has just changed. Every listed
line that states the old value, since the same fact can be listed more than
once in different words. "We switched from PyTorch to JAX" replaces every line
saying they use PyTorch, and its statement says they now use JAX instead of
PyTorch. Empty unless the person changed something: a new fact that sits
alongside an old one replaces nothing.

subject, relation, object: the same fact as a link in a knowledge graph.
subject is who or what it is about, by name (the person by their name,
"Kavya", "GreenLoop", "the thesis"); object is the other end, a name or a value
("KJ Somaiya", "Meera", "15 December 2026", "50 lakh rupees"); relation is a
short verb phrase in capitals with underscores (STUDIES_AT, IS_SISTER_OF,
HAS_DEADLINE, USES, HANDLES). For "Kavya is Meera's sister": subject "Kavya",
relation "IS_SISTER_OF", object "Meera". For "The thesis deadline is 15
December 2026": subject "the thesis", relation "HAS_DEADLINE", object "15
December 2026". Copy names exactly as they appear in the statement, and use the
same name for the same thing every time. The statement stays a plain sentence.

reason: first, a short phrase explaining the decision, for a human reading the
logs.
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
    # The fact as a graph link; empty when it has none (words kept verbatim).
    subject: str = ""
    relation: str = ""
    object: str = ""


@dataclass(frozen=True)
class GateDecision:
    persist: bool
    reason: str
    facts: tuple[GateFact, ...] = ()
    # 1-based positions in the known facts the gate was shown, of those this
    # turn made untrue. Read even when nothing new is persisted: "I no longer
    # use Neo4j" retires a fact without adding one.
    replaces: tuple[int, ...] = ()

    @classmethod
    def skip(cls, reason: str, replaces: tuple[int, ...] = ()) -> "GateDecision":
        return cls(persist=False, reason=reason, replaces=replaces)


# Whole-message pleasantries. Deliberately an exact-match list rather than a
# length cutoff: "I use Neo4j" is eleven characters and matters, while "thanks
# so much for that!" is long and does not.
_TRIVIAL = {
    "hi", "hey", "hello", "yo", "sup",
    "thanks", "thank you", "ty", "thx", "cheers", "much appreciated",
    "ok", "okay", "k", "cool", "nice", "great", "perfect", "got it", "sounds good",
    "yes", "no", "yep", "nope", "sure", "please", "bye", "goodbye", "see you",
}


_FIRST_PERSON = re.compile(r"\b(i|i'm|im|i've|i'll|i'd|me|my|mine|we|we're|our|ours)\b", re.IGNORECASE)


def only_asks(text: str) -> bool:
    """Every sentence a question, and none of them about the person: "Who
    climbed them, and how often?" ("Can you remember my deadline is
    Friday?" still goes to the model)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return bool(sentences) and all(s.endswith("?") for s in sentences) and not _FIRST_PERSON.search(text)


_HYPOTHETICAL = re.compile(
    r"^\s*(?:if|when)\b[^.?!]*\b(?:i'd|i would|we'd|we would|i'll|i will)\b"
    r"|\b(?:someday|some day|one day i|maybe i'll|maybe we'll|i might|we might)\b",
    re.IGNORECASE)


def is_hypothetical(text: str) -> bool:
    """One sentence that only imagines: "If I had more time I'd learn Rust".
    The model kept it as "wants to learn Rust" despite being told not to."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return len(sentences) == 1 and bool(_HYPOTHETICAL.search(text))


_SWITCH = re.compile(
    r"\b(?:switched|moved|changed|migrated|went)\s+(?:over\s+)?from\s+(?P<a>[\w.+#-]+(?:\s[\w.+#-]+)?)\s+to\b"
    r"|\binstead\s+of\s+(?P<b>[\w.+#-]+(?:\s[\w.+#-]+)?)"
    r"|\bno\s+longer\s+(?:use|using|uses|on|with|at|in)?\s*(?P<c>[\w.+#-]+)"
    r"|\breplaced\s+(?P<d>[\w.+#-]+(?:\s[\w.+#-]+)?)\s+with\b",
    re.IGNORECASE)


def replaced_terms(text: str) -> list[str]:
    """What a change of mind says is no longer so: "switched from PyTorch to
    JAX" -> ["PyTorch"]. Used when the model names nothing to replace."""
    out = []
    for m in _SWITCH.finditer(text):
        term = next(g for g in (m["a"], m["b"], m["c"], m["d"]) if g).strip()
        if term.lower() not in ("the", "a", "it", "that", "this"):
            out.append(term)
    return out


_CONTENT_STOP = set("""a an the is are was were be been am i me my mine we our us you your he she they them it its of to
in on at for with and or but do does did have has had this that these those there here about can could would
should will just really very also so then than as by from into its it's i'm we're that's actually called named
remember please thanks changed change switched moved updated update instead now still anymore going""".split())


def uncovered_words(message: str, statements: list[str]) -> list[str]:
    """Words of the message (four letters or more, not filler) that none of
    the kept statements contains. "It's a recycling marketplace" kept as
    "GreenLoop is a startup" leaves ["recycling", "marketplace"]."""
    said = set(re.findall(r"[a-z0-9]+", " ".join(statements).lower()))
    said |= {w.rstrip("s") for w in said}
    words = re.findall(r"[a-z0-9]+", message.lower())
    return [w for w in dict.fromkeys(words)
            if len(w) >= 4 and w not in _CONTENT_STOP and w not in said and w.rstrip("s") not in said]


_SLASHED = re.compile(r"^\s*(.+?)\s*/\s*([A-Z][A-Z0-9_]*)\s*/\s*(.+?)\s*\.?\s*$")


def _closest_span(name: str, statement: str) -> str:
    """The run of words in ``statement`` most like ``name``: the model wrote
    "Mea ra Iyer" and "K/Somaiya" for names the statement spells right."""
    import difflib

    if not name or name.lower() in statement.lower():
        return name
    words = statement.rstrip(".").split()
    best, best_ratio = name, 0.0
    for size in range(1, min(5, len(words)) + 1):
        for i in range(len(words) - size + 1):
            span = " ".join(words[i:i + size]).strip(",;:'\"")
            span = re.sub(r"[’']s$", "", span)
            # Letters and digits only: punctuation the model invented ("K/")
            # shouldn't favour a shorter span.
            ratio = difflib.SequenceMatcher(None, re.sub(r"[^a-z0-9]", "", name.lower()),
                                            re.sub(r"[^a-z0-9]", "", span.lower())).ratio()
            if ratio > best_ratio:
                best, best_ratio = span, ratio
    return best if best_ratio >= 0.6 else name


def _repaired(fact: "GateFact") -> "GateFact":
    """A fact's triple made to agree with its own sentence."""
    statement = fact.statement
    slashed = _SLASHED.match(statement)
    if slashed:
        # "The thesis / HAS_DEADLINE / 15 December 2026", copied from the
        # prompt's own example format: written back out as a sentence.
        subj, rel, obj = slashed.groups()
        statement = f"{subj} {rel.lower().replace('_', ' ')} {obj}."
    return GateFact(statement=statement, scope=fact.scope,
                    subject=_closest_span(fact.subject, statement), relation=fact.relation,
                    object=_closest_span(fact.object, statement))


def heal_words(text: str, message: str) -> str:
    """Rejoin a word the model split in two ("Kavy a lives in Bangalore") when
    the halves together are a word the person wrote. Seen once or twice a run
    under concurrent load; the cause wasn't pinned down."""
    said = {w.lower() for w in re.findall(r"[A-Za-z]+", message)}
    words = text.split(" ")
    out, i = [], 0
    while i < len(words):
        a = words[i]
        b = words[i + 1] if i + 1 < len(words) else ""
        a_core, b_core = re.sub(r"\W", "", a).lower(), re.sub(r"\W", "", b).lower()
        if b and a_core and b_core and (a_core + b_core) in said and a_core not in said and len(b_core) <= 3:
            out.append(a + b.lstrip())
            i += 2
            continue
        out.append(a)
        i += 1
    return " ".join(out)


def _healed(decision: "GateDecision", message: str) -> "GateDecision":
    if not decision.persist:
        return decision
    facts = tuple(GateFact(heal_words(f.statement, message), f.scope, heal_words(f.subject, message),
                           f.relation, heal_words(f.object, message)) for f in decision.facts)
    return GateDecision(persist=True, reason=decision.reason, facts=facts, replaces=decision.replaces)


def _drop_unsaid_dates(decision: "GateDecision", user_message: str, said_at) -> "GateDecision":
    """A fact may only carry dates the message gives, written out or relative
    ("next Friday", worked out in clock.py). The model once copied a date from
    memory into a new fact ("presenting on 3 November", said with no date)
    and once miscounted "next Friday" as a Monday."""
    from .clock import ORDINALS, dates_in, relative_dates

    if not decision.persist:
        return decision
    today = (said_at or datetime.now().astimezone()).astimezone().date()
    allowed = set(dates_in(user_message, today)) | {d for _, d in relative_dates(user_message, today)}
    # "Moved to the 21st" (or "the twenty-first"): a day with no month still
    # vouches for that day.
    days = {int(m[1]) for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)\b", user_message)}
    days |= {n for word, n in ORDINALS.items() if re.search(rf"\b{re.escape(word)}\b", user_message, re.I)}
    kept = []
    for fact in decision.facts:
        unsaid = [d for d in dates_in(fact.statement, today) if d not in allowed and d.day not in days]
        if unsaid:
            logger.info("gate fact dropped, dates not in the message %s: %s", unsaid, fact.statement)
            continue
        kept.append(fact)
    if len(kept) == len(decision.facts):
        return decision
    if not kept:
        return GateDecision.skip(f"facts carried dates the person didn't give ({decision.reason})",
                                 decision.replaces)
    return GateDecision(persist=True, reason=decision.reason, facts=kept, replaces=decision.replaces)


def _drop_own_name(decision: "GateDecision", subject_name: str) -> "GateDecision":
    """"Aarya's name is Aarya": the account already holds the name (core sets
    it when the person states it), and the model writes it anyway."""
    if not decision.persist or not subject_name:
        return decision
    own = re.compile(rf"^\s*{re.escape(subject_name)}(?:'s name is| is called| goes by)\s+{re.escape(subject_name)}\W*$",
                     re.IGNORECASE)
    kept = [f for f in decision.facts if not own.match(f.statement)]
    if len(kept) == len(decision.facts):
        return decision
    if not kept:
        return GateDecision.skip(f"only the person's own name ({decision.reason})", decision.replaces)
    return GateDecision(persist=True, reason=decision.reason, facts=kept, replaces=decision.replaces)


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
    replaces = tuple(n for n in (data.get("replaces") or []) if isinstance(n, int) and n > 0)
    if not data.get("persist"):
        return GateDecision.skip(reason or "gate declined", replaces)

    entries = data.get("facts")
    if not isinstance(entries, list):
        return GateDecision.skip("gate said persist but gave no facts list", replaces)

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
        facts.append(_repaired(GateFact(statement=statement, scope=scope,
                                        subject=str(entry.get("subject") or "").strip(),
                                        relation=str(entry.get("relation") or "").strip(),
                                        object=str(entry.get("object") or "").strip())))

    if not facts:
        return GateDecision.skip(f"gate said persist but gave no usable facts ({reason})", replaces)

    if len(facts) > MAX_FACTS_PER_TURN:
        logger.info(
            "gate emitted %d facts; keeping the first %d",
            len(facts),
            MAX_FACTS_PER_TURN,
        )
        facts = facts[:MAX_FACTS_PER_TURN]

    return GateDecision(persist=True, reason=reason, facts=tuple(facts), replaces=replaces)


async def decide(
    provider: Provider,
    *,
    user_message: str,
    assistant_message: str,
    subject_name: str,
    known_facts: Sequence[str] = (),
    model: str | None = None,
    said_at: datetime | None = None,
    project_name: str | None = None,
) -> GateDecision:
    """What this turn leaves behind, and where each piece of it goes.

    Never raises. A gate that fails means nothing is written, which is the safe
    direction: a missing memory is recoverable by saying the thing again, a
    wrong one has to be found first.
    """
    if looks_trivial(user_message):
        return GateDecision.skip("pre-filtered: message is a pleasantry")
    if only_asks(user_message):
        return GateDecision.skip("pre-filtered: only questions, nothing said about the person")
    if is_hypothetical(user_message):
        return GateDecision.skip("pre-filtered: a hypothetical, not a fact")

    sections = [
        f"The person is named: {subject_name}",
        # The gate runs from the write queue, possibly long after the turn:
        # when it was said is what "tomorrow" and "next Friday" mean.
        f"This was said on {describe_now(said_at)}.",
    ]
    # Relative dates resolved here, by rule: the model miscounts them.
    from .clock import relative_dates

    resolved = relative_dates(user_message, (said_at or datetime.now().astimezone()).astimezone().date())
    if resolved:
        sections.append("Relative dates in it, as actual dates (write these): " + "; ".join(
            f"\"{phrase}\" = {d.strftime('%A')} {d.day} {d.strftime('%B %Y')}" for phrase, d in resolved))
    if known_facts:
        already = "\n".join(f"[{n}] {f}" for n, f in enumerate(known_facts, 1))
        sections.append(f"Already in memory, numbered; do not restate:\n{already}")
    sections.append(f"{subject_name} said:\n{user_message}")
    sections.append(f"The assistant replied (context only, not a source):\n{assistant_message}")
    sections.insert(1, f"This conversation is in the project \"{project_name}\": a fact about this work "
                       f"says so (\"for {project_name}\")." if project_name else
                    "This conversation is not in any project.")
    messages = [{"role": "system", "content": GATE_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(sections)}]

    async def ask(msgs) -> str:
        return await provider.chat(
            msgs,
            model=model,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "memory_gate", "schema": GATE_SCHEMA},
            },
            # A decision, not prose: at the runtime's default 0.7 the same
            # introduction was kept twice and dropped once, with the same
            # stated reason each time (scenario suite, 2026-10-04).
            temperature=0.0,
        )

    try:
        raw = await ask(messages)
    except Exception as exc:
        logger.warning("Write gate call failed: %s", exc)
        return GateDecision.skip(f"gate call failed: {exc.__class__.__name__}")

    decision = _healed(_parse(raw), user_message)
    missing = uncovered_words(user_message, [f.statement for f in decision.facts]) if decision.persist else []
    if missing:
        # Once more, naming what was left out: asked for triples too, the 8B
        # model kept one fact of "My co-founder Arjun handles sales and I handle
        # the tech" (2026-10-13). Only on turns that lost something.
        try:
            again = _healed(_parse(await ask(messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"You left out: {', '.join(missing[:8])}. Add a fact for each that is "
                                            "about the person, their work or someone they named, keep the facts "
                                            "you had, and return the whole JSON again."},
            ])), user_message)
            if again.persist and len(again.facts) > len(decision.facts):
                logger.info("gate second pass for %s: %d -> %d facts", missing[:8], len(decision.facts), len(again.facts))
                decision = GateDecision(persist=True, reason=decision.reason, facts=again.facts,
                                        replaces=decision.replaces or again.replaces)
        except Exception as exc:
            logger.warning("gate second pass failed: %s", exc)
    decision = _drop_unsaid_dates(decision, user_message, said_at)
    decision = _drop_own_name(decision, subject_name)

    if decision.persist and known_facts:
        surviving, dropped = drop_known(decision.facts, known_facts)
        for note in dropped:
            logger.info("gate restated a recalled fact; dropped  %s", note)
        if not surviving:
            return GateDecision.skip(
                f"every proposed fact was already in memory ({decision.reason})", decision.replaces
            )
        decision = GateDecision(
            persist=True, reason=decision.reason, facts=surviving, replaces=decision.replaces
        )

    logger.info(
        "gate persist=%s reason=%s facts=%s replaces=%s",
        decision.persist,
        decision.reason,
        [(f.scope, f.statement[:70]) for f in decision.facts],
        list(decision.replaces),
    )
    return decision
