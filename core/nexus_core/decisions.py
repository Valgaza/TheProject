"""Decisions that do not need a language model.

Some model calls in a turn are really classifications: "is this worth
remembering?", "is this person asking me to search the web?". A decision model
(Laya, a 421M-parameter encoder served by the runtime) answers questions like
these in one forward pass, ~10-15 ms, with calibrated probabilities instead of
generated text.

It is not trusted on arrival. Each decision point has a mode:

- ``off``: not asked.
- ``shadow``: asked, its answer recorded in ``decision_log`` beside what the
  existing logic decided, and ignored. This is how it earns trust.
- ``live``: its answer is acted on, within the thresholds below.

scripts/laya/evaluate.py measures shadow agreement; a point moves to live only
past the bar recorded in the milestone plan.

Every call degrades: an unreachable runtime, a missing model, a timeout -- all
return None, and the caller does what it did before Laya existed.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Generous for a ~15 ms model, because the call sits on a live turn: better to
# skip the decision than to make a person wait for it.
DECIDE_TIMEOUT_SECONDS = 1.5


# ── The questions ────────────────────────────────────────────────────────────
# Worded for an encoder that reads the question and the state together. The
# state keys are named in backticks the way Laya's own presets do.

# Phrased as two-way choices rather than yes/no ("noul") questions: measured
# on the labelled sets, the choice wording separated the classes markedly
# better zero-shot (gate AUC 0.67 -> 0.81; web 0.94 -> 0.96), in line with
# Laya's own note that noul questions can be label-biased.
GATE_QUESTIONS: dict[str, dict] = {
    "worth_remembering": {
        "type": "choice",
        "instructions": "What is `person_said`?",
        "criteria": {
            "lasting_fact": "the person tells a lasting fact about themselves, their "
                            "preferences, plans, decisions or their work",
            "other": "a question, a request for help, a greeting or thanks, or a "
                     "passing remark about right now",
        },
    },
    "tier": {
        "type": "choice",
        "instructions": "Where would what `person_said` belong?",
        "criteria": {
            "user": "the person in general: identity, background, preferences, habits",
            "project": "one specific piece of work: its tools, decisions, people, deadlines",
        },
    },
}

WEB_INTENT_QUESTIONS: dict[str, dict] = {
    "asks_for_web": {
        "type": "choice",
        "instructions": "Where does `message` ask the assistant to get the answer from?",
        "criteria": {
            "web": "searching the internet or looking something up online",
            "itself": "its own knowledge, the conversation, or the person's documents",
        },
    },
}

# For each two-way choice, the option that means "yes" -- so callers read one
# probability whichever form a question takes.
YES_OPTION = {"worth_remembering": "lasting_fact", "asks_for_web": "web"}

WEB_QUERY_QUESTIONS: dict[str, dict] = {
    "identifies_someone": {
        "type": "noul",
        "instructions": (
            "Does `query` contain a specific person's name, an ID or case number, "
            "an email address, or another detail that identifies a particular "
            "person, client or patient?"
        ),
    },
}


@dataclass(frozen=True)
class Decision:
    answers: dict[str, dict[str, Any]]
    ms: int

    def p(self, question: str) -> float | None:
        """Probability of "yes": a noul answer's own, or a two-way choice's
        probability of the option that means yes (YES_OPTION)."""
        answer = self.answers.get(question) or {}
        if answer.get("p") is not None:
            return float(answer["p"])
        probs = answer.get("probs") or {}
        option = YES_OPTION.get(question)
        return float(probs[option]) if option in probs else None

    def label(self, question: str) -> str | None:
        """The most likely option of a choice question."""
        answer = self.answers.get(question) or {}
        return answer.get("label")


class DecisionsClient:
    """The runtime's /decide endpoint, failing soft."""

    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def decide(self, state: dict[str, str], questions: dict[str, dict]) -> Decision | None:
        started = time.perf_counter()
        try:
            response = await self._http.post(
                f"{self._base_url}/decide",
                json={"state": state, "questions": questions},
                timeout=DECIDE_TIMEOUT_SECONDS,
                headers={"X-Nexus-Priority": "live"},
            )
            response.raise_for_status()
            answers = response.json()["answers"]
        except Exception as exc:
            logger.warning("decision model unavailable, using existing logic: %s", exc)
            return None
        return Decision(answers=answers, ms=round((time.perf_counter() - started) * 1000))


def runtime_root(openai_base_url: str | None) -> str | None:
    """The runtime's own URL, from the OpenAI-compatible one that points at it.
    None when inference is not on the runtime (Ollama has no /decide)."""
    if not openai_base_url or ":11434" in openai_base_url:
        return None
    return openai_base_url.rstrip("/").removesuffix("/v1")


def log_entry(
    *,
    point: str,
    mode: str,
    state: dict,
    decision: Decision | None,
    baseline: dict | None = None,
    chat_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
):
    """A decision_log row, for the caller to add to its session."""
    from .models import DecisionLogRecord

    return DecisionLogRecord(
        point=point,
        mode=mode,
        chat_id=chat_id,
        message_id=message_id,
        input=state,
        laya=decision.answers if decision else None,
        baseline=baseline,
        laya_ms=decision.ms if decision else None,
        error=None if decision else "decision model unavailable",
    )
