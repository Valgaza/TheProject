"""How well the decision model (Laya) makes each decision core asks of it.

Runs the hand-labelled sets in scripts/laya/labels/ through the runtime's
/decide endpoint with exactly the questions core uses (nexus_core.decisions),
and reports, per decision point:

- accuracy at 0.5 and calibration (expected calibration error, 10 bins);
- the live threshold the switch-over rule allows, and what it buys:
    gate        skip the LLM below T only while at most 1 in 20 skipped turns
                was actually worth remembering (the bar in the milestone plan)
    web_intent  route to the web at or above T only with zero false positives
                on the labelled negatives -- a false yes sends words off the
                machine
- agreement with the LLM gate on real shadow-mode turns (decision_log), when
  there are any.

    uv run python scripts/laya/evaluate.py --out docs/benchmarks/laya-zero-shot

The labelled sets were drafted to start the evaluation; they are small (100
gate turns, 65 messages) and should grow from real, reviewed decisions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
LABELS = Path(__file__).resolve().parent / "labels"
sys.path.insert(0, str(ROOT / "core"))
from nexus_core.decisions import GATE_QUESTIONS, WEB_INTENT_QUESTIONS, Decision  # noqa: E402

MAX_WRONG_SKIP_RATE = 0.05


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (LABELS / name).read_text().splitlines() if line.strip()]


def ece(probs: list[float], labels: list[bool], bins: int = 10) -> float:
    total, err = len(probs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(probs[i] for i in idx) / len(idx)
            acc = sum(labels[i] for i in idx) / len(idx)
            err += len(idx) / total * abs(conf - acc)
    return round(err, 4)


def gate_threshold(probs: list[float], worth: list[bool]) -> dict:
    """Highest T such that, among turns with p < T, at most 5% were worth
    remembering. Reports how many of the truly-not-worth turns it skips."""
    best = {"threshold": 0.0, "skipped": 0, "wrongly_skipped": 0, "coverage_of_declines": 0.0}
    declines = sum(1 for w in worth if not w)
    for t in sorted({0.0, *[round(p + 1e-6, 6) for p in probs]}):
        skipped = [w for p, w in zip(probs, worth) if p < t]
        wrong = sum(skipped)
        if skipped and wrong / len(skipped) <= MAX_WRONG_SKIP_RATE:
            best = {
                "threshold": round(t, 4),
                "skipped": len(skipped),
                "wrongly_skipped": wrong,
                "coverage_of_declines": round((len(skipped) - wrong) / declines, 3) if declines else 0.0,
            }
    return best


def web_threshold(probs: list[float], asks: list[bool]) -> dict:
    """Lowest T with no false positives; reports the recall it keeps."""
    negatives = [p for p, a in zip(probs, asks) if not a]
    t = round(max(negatives) + 1e-4, 4) if negatives else 0.5
    caught = sum(1 for p, a in zip(probs, asks) if a and p >= t)
    positives = sum(asks)
    return {"threshold": t, "recall": round(caught / positives, 3) if positives else 0.0,
            "false_positives": 0}


async def _decide(http, state, questions):
    r = await http.post("/decide", json={"state": state, "questions": questions})
    r.raise_for_status()
    return r.json()["answers"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    parser.add_argument("--title", default="zero-shot",
                        help="which checkpoint this is, for the report heading (e.g. 'fine-tuned, round 3')")
    parser.add_argument("--runtime", help="the runtime to ask (default: from OPENAI_BASE_URL), e.g. a "
                        "second one serving another checkpoint")
    args = parser.parse_args()
    env = dotenv_values(ROOT / ".env")
    root = args.runtime or (env.get("OPENAI_BASE_URL") or "http://localhost:8003/v1").removesuffix("/v1")
    headers = {"X-Service-Token": env.get("SERVICE_TOKEN") or ""}

    report: dict = {}
    async with httpx.AsyncClient(base_url=root, headers=headers, timeout=60) as http:
        # ── gate ──
        rows = _load("gate.jsonl")
        probs, worth, tiers_ok, tiers_n, misses = [], [], 0, 0, []
        for row in rows:
            state = {"person_said": row["person_said"], "assistant_replied": row["assistant_replied"]}
            a = await _decide(http, state, GATE_QUESTIONS)
            p = Decision(a, 0).p("worth_remembering")
            probs.append(p)
            worth.append(bool(row["worth_remembering"]))
            if row["worth_remembering"] and row["tier"]:
                tiers_n += 1
                tiers_ok += a["tier"]["label"] == row["tier"]
            if (p >= 0.5) != bool(row["worth_remembering"]):
                misses.append({"said": row["person_said"], "label": row["worth_remembering"], "p": round(p, 3)})
        report["gate"] = {
            "n": len(rows),
            "accuracy_at_0_5": round(sum((p >= 0.5) == w for p, w in zip(probs, worth)) / len(rows), 3),
            "ece": ece(probs, worth),
            "tier_accuracy": round(tiers_ok / tiers_n, 3) if tiers_n else None,
            "tier_n": tiers_n,
            "live_rule": gate_threshold(probs, worth),
            "mistakes_at_0_5": misses,
        }
        # ── web intent ──
        rows = _load("web_intent.jsonl")
        probs, asks, misses = [], [], []
        for row in rows:
            a = await _decide(http, {"message": row["message"]}, WEB_INTENT_QUESTIONS)
            p = Decision(a, 0).p("asks_for_web")
            probs.append(p)
            asks.append(bool(row["asks_for_web"]))
            if (p >= 0.5) != bool(row["asks_for_web"]):
                misses.append({"message": row["message"], "label": row["asks_for_web"], "p": round(p, 3)})
        report["web_intent"] = {
            "n": len(rows),
            "accuracy_at_0_5": round(sum((p >= 0.5) == a for p, a in zip(probs, asks)) / len(rows), 3),
            "ece": ece(probs, asks),
            "live_rule": web_threshold(probs, asks),
            "mistakes_at_0_5": misses,
        }

    # ── shadow agreement with the LLM gate on real turns ──
    try:
        db = await asyncpg.connect(env["DATABASE_URL"])
        rows = await db.fetch(
            "select laya, baseline from decision_log where point = 'gate' and laya is not null "
            "and baseline is not null"
        )
        await db.close()
        pairs = [((Decision(json.loads(r["laya"]), 0).p("worth_remembering") or 0) >= 0.5,
                  json.loads(r["baseline"])["persist"]) for r in rows]
        report["gate_shadow_agreement"] = {
            "n": len(pairs),
            "agreement": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
        }
    except Exception as exc:
        report["gate_shadow_agreement"] = {"error": str(exc)}

    g, w = report["gate"], report["web_intent"]
    md = "\n".join([
        f"# Laya, {args.title}, on Nexus's decisions", "",
        "Hand-labelled sets in `scripts/laya/labels/` through the runtime's `/decide`, with the questions "
        "core asks (`core/nexus_core/decisions.py`).", "",
        "| Point | n | Accuracy at 0.5 | ECE | Live threshold | What it buys |",
        "|---|---|---|---|---|---|",
        f"| gate: worth remembering | {g['n']} | {g['accuracy_at_0_5']:.0%} | {g['ece']} | "
        f"skip below {g['live_rule']['threshold']} | skips {g['live_rule']['coverage_of_declines']:.0%} of "
        f"declines, {g['live_rule']['wrongly_skipped']} wrongly |",
        f"| gate: tier (on worth-remembering turns) | {g['tier_n']} | {g['tier_accuracy']:.0%} | | | |",
        f"| web intent | {w['n']} | {w['accuracy_at_0_5']:.0%} | {w['ece']} | "
        f"web at or above {w['live_rule']['threshold']} | recall {w['live_rule']['recall']:.0%}, "
        "no false positives |",
        "",
        f"Shadow agreement with the LLM gate on real turns: {report['gate_shadow_agreement']}", "",
        "## Gate mistakes at 0.5", "", *[f"- p={m['p']} label={m['label']}: {m['said']}" for m in g["mistakes_at_0_5"]],
        "", "## Web-intent mistakes at 0.5", "",
        *[f"- p={m['p']} label={m['label']}: {m['message']}" for m in w["mistakes_at_0_5"]], "",
    ])
    print(md)
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        (out.parent / f"{out.name}.json").write_text(json.dumps(report, indent=2))
        (out.parent / f"{out.name}.md").write_text(md)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
