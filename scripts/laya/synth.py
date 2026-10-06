"""Generate labelled training examples for the decision model with the local LLM.

The hand-labelled sets in labels/ are held out for evaluation only, so the
model is never scored on what it was trained on. Training data comes from
here: the resident chat model writes varied examples *of a category it is
told*, and the label is that category -- so a label is a design decision, not
the LLM's judgement of an example it then also learns from.

Categories mirror the decisions in core/nexus_core/decisions.py:

  gate        lasting facts about the person (user tier), lasting facts about
              one piece of work (project tier), and everything that should
              not be remembered -- questions, requests, thanks, passing
              remarks, and questions the assistant answers from memory
  web_intent  requests to look something up online, phrased many ways, and
              hard negatives that mention the internet without asking for it

    uv run python scripts/laya/synth.py --per-category 120

Writes data/laya/synth_gate.jsonl and data/laya/synth_web.jsonl (local only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "laya"

DOMAINS = ["software engineering", "a law firm", "a hospital ward", "a university thesis",
           "a small bakery", "a research lab", "personal finance", "home renovation",
           "a startup", "school teaching", "journalism", "a design agency"]

GATE_CATEGORIES = {
    "user": ("a lasting fact the person states about themselves: identity, background, family, "
             "health, location, habits, skills, or a preference for how the assistant should "
             "answer them", True, "user"),
    "project": ("a lasting fact the person states about one specific piece of their work in {domain}: "
                "a tool chosen, a decision, a deadline, who owns what, a requirement or constraint",
                True, "project"),
    # Added after the first fine-tune missed these kinds of personal facts
    # (allergies, partners, birthdays, languages, team moves, new tools).
    "user_personal": ("a lasting personal fact the person states in passing: a health condition or "
                      "allergy, a family member or partner's name, a birthday or anniversary, a "
                      "language they speak, a job change, or a new tool or habit they adopted",
                      True, "user"),
    "project_detail": ("a lasting fact about one piece of their work in {domain}, said casually: an "
                       "agreed convention, a requirement from a client, a launch date, who joined "
                       "the team, a format the deliverable must follow", True, "project"),
    "question": ("a question the person asks the assistant about general knowledge or how to do "
                 "something", False, None),
    "request": ("a task the person asks for: write, translate, summarise, rephrase, fix, explain, "
                "list or convert something", False, None),
    "smalltalk": ("a greeting, thanks, acknowledgement, or one-word reaction", False, None),
    "transient": ("a remark about the person's state right now that will not matter tomorrow: "
                  "mood, weather, what they are doing this minute", False, None),
    "recall": ("a question asking the assistant to recall something the person told it before, "
               "where the assistant's reply states that fact back", False, None),
}

WEB_CATEGORIES = {
    "asks": ("a message asking the assistant to search the internet or look something up online, "
             "about {domain}; vary the wording a lot (search, google, look up, check online, find "
             "the latest, browse, research online)", True),
    "local": ("a message asking the assistant something it can answer from its own knowledge or the "
              "conversation, about {domain}, with no request to go online", False),
    # Added after round two missed indirect requests and flagged plain tasks.
    "asks_indirect": ("a message that wants current information from the web but asks indirectly, e.g. "
                      "'what does the internet say about ...', 'check the web: ...', 'what are people "
                      "saying online about ...', 'is ... still true according to the latest news', about "
                      "{domain}", True),
    "local_tasks": ("a message asking the assistant to list, define, explain, compare, calculate, convert "
                    "or brainstorm something about {domain}, using its own knowledge, with no request to "
                    "go online", False),
    "mentions": ("a message that mentions the internet, websites, browsers or searching without asking "
                 "the assistant to search: e.g. a statement about something they read online, or a "
                 "question about how the web works", False),
}

SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"person_said": {"type": "string"},
                                     "assistant_replied": {"type": "string"}},
    "required": ["person_said", "assistant_replied"]}}}, "required": ["items"]}


async def _batch(http, model: str, description: str, n: int, seed: int) -> list[dict]:
    prompt = (
        f"Write {n} different realistic chat messages a person might send to a personal assistant. "
        f"Each must be {description}. Make them varied in length, tone and wording, first person, "
        "and natural. For each, also write the assistant's short reply (one sentence). "
        f"Variation seed: {seed}."
    )
    r = await http.post("/v1/chat/completions", json={
        "model": model, "temperature": 1.0, "max_tokens": 3000,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "examples", "schema": SCHEMA}},
    }, headers={"X-Nexus-Priority": "bulk"})
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])["items"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--per-category", type=int, default=120)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--only", nargs="*", help="generate only these gate categories, appended")
    args = parser.parse_args()
    env = dotenv_values(ROOT / ".env")
    base = (env.get("OPENAI_BASE_URL") or "http://localhost:8003/v1").removesuffix("/v1")
    model = env.get("CORE_CHAT_MODEL")
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)

    async with httpx.AsyncClient(base_url=base, timeout=600,
                                 headers={"Authorization": f"Bearer {env.get('SERVICE_TOKEN')}"}) as http:
        existing = OUT / "synth_gate.jsonl"
        gate_rows = [json.loads(l) for l in existing.read_text().splitlines()] if args.only and existing.exists() else []
        seen = {r["person_said"].lower() for r in gate_rows}
        for name, (desc, worth, tier) in GATE_CATEGORIES.items():
            if args.only and name not in args.only:
                continue
            got = 0
            while got < args.per_category:
                d = desc.format(domain=rng.choice(DOMAINS))
                for item in await _batch(http, model, d, args.batch, rng.randint(1, 10**6)):
                    said = item["person_said"].strip()
                    if not said or said.lower() in seen:
                        continue
                    seen.add(said.lower())
                    gate_rows.append({"person_said": said, "assistant_replied": item["assistant_replied"].strip(),
                                      "worth_remembering": worth, "tier": tier, "category": name})
                    got += 1
            print(f"gate/{name}: {got}", flush=True)
        (OUT / "synth_gate.jsonl").write_text("".join(json.dumps(r) + "\n" for r in gate_rows))
        if args.only and not any(o in WEB_CATEGORIES for o in args.only):
            print(f"gate now {len(gate_rows)} examples")
            return 0

        existing_web = OUT / "synth_web.jsonl"
        web_rows = ([json.loads(l) for l in existing_web.read_text().splitlines()]
                    if args.only and existing_web.exists() else [])
        seen = {r["message"].lower() for r in web_rows}
        for name, (desc, asks) in WEB_CATEGORIES.items():
            if args.only and name not in args.only:
                continue
            got = 0
            while got < args.per_category:
                d = desc.format(domain=rng.choice(DOMAINS))
                for item in await _batch(http, model, d, args.batch, rng.randint(1, 10**6)):
                    msg = item["person_said"].strip()
                    if not msg or msg.lower() in seen:
                        continue
                    seen.add(msg.lower())
                    web_rows.append({"message": msg, "asks_for_web": asks, "category": name})
                    got += 1
            print(f"web/{name}: {got}", flush=True)
        (OUT / "synth_web.jsonl").write_text("".join(json.dumps(r) + "\n" for r in web_rows))
    print(f"wrote {len(gate_rows)} gate and {len(web_rows)} web examples to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
