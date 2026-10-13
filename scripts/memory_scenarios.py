"""How well memory forms, stays in its tier, and answers: a scripted run.

A throwaway user tells Nexus facts about themselves (loose chats: the user
tier) and about two projects (each its own tier): third parties, a mixed
message, a correction, a relative date, a question that carries a fact. Once
every write has settled, it dumps each tier of the graph and asks recall
questions from a fresh chat in each place, scoring every answer on details it
must contain and details it must not (another tier's facts, a superseded value).

    uv run python scripts/memory_scenarios.py [--out docs/benchmarks/memory-scenarios]

Needs the full stack running. Deletes everything it created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from conftest import _remove_user  # noqa: E402

CORE = "http://localhost:8100"
MEMORY = "http://localhost:8000"

# (where, statement). "user" is a loose chat; otherwise a project name.
STATEMENTS = [
    ("user", "My name is Meera Iyer and I'm a final-year computer engineering student at KJ Somaiya."),
    ("user", "I'm vegetarian and allergic to peanuts."),
    ("user", "My sister Kavya lives in Bangalore and works as a doctor."),
    ("user", "I prefer answers in bullet points."),
    ("user", "Kavya prefers tea over coffee."),
    ("user", "Can you remember that my driving test is on 9 November 2026?"),
    ("Thesis", "This thesis is on federated learning for medical imaging."),
    ("Thesis", "My supervisor for the thesis is Dr. Rao, and we meet every Thursday."),
    ("Thesis", "The thesis submission deadline is 15 December 2026."),
    ("Thesis", "We're using PyTorch and the dataset is CheXpert."),
    ("Thesis", "Actually we switched from PyTorch to JAX."),
    ("Startup", "The startup is called GreenLoop, it's a recycling marketplace."),
    ("Startup", "My co-founder Arjun handles sales and I handle the tech."),
    ("Startup", "Our seed round target is 50 lakh rupees."),
    ("Startup", "We launch the beta next Monday."),
    ("Startup", "For the startup we chose Postgres, and personally I hate early mornings."),
    ("Startup", "What should a recycling marketplace charge sellers?"),
    ("Startup", "thanks, that's useful"),
    ("Startup", "If I had more time I'd learn Rust."),
]


UNKNOWN = "don't know|do not know|not sure|haven't|have not|no information|not mentioned|didn't mention"


def next_monday(today: date) -> date:
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


def questions(today: date) -> list[dict]:
    beta = next_monday(today)
    return [
        # Loose chat: the user tier only.
        {"where": "user", "q": "What's my full name and where do I study?", "must": ["Meera", "Somaiya"], "not": []},
        {"where": "user", "q": "Do I have any dietary restrictions?", "must": ["vegetarian", "peanuts?"], "not": []},
        {"where": "user", "q": "Who is Kavya, where does she live and what does she do?", "must": ["sister", "Bangalore", "doctor"], "not": []},
        {"where": "user", "q": "Do I want to learn Rust?", "must": [], "not": ["yes", "^you want to learn", "you do want"]},
        {"where": "user", "q": "Does Kavya prefer tea or coffee?", "must": ["tea"], "not": []},
        {"where": "user", "q": "When is my driving test?", "must": ["9 November"], "not": []},
        {"where": "user", "q": "What is my thesis about?", "must": [UNKNOWN], "not": ["federated", "medical imaging", "Rust"]},
        {"where": "user", "q": "Who is my co-founder?", "must": [], "not": ["Arjun"]},
        # Thesis: its own tier, plus the user tier.
        {"where": "Thesis", "q": "Who is my supervisor and when do we meet?", "must": ["Rao", "Thursday"], "not": []},
        {"where": "Thesis", "q": "When is the submission deadline?", "must": ["15 December"], "not": []},
        {"where": "Thesis", "q": "Which framework and dataset are we using?", "must": ["JAX", "CheXpert"], "not": ["using PyTorch", "JAX and PyTorch", "PyTorch and JAX"]},
        {"where": "Thesis", "q": "Am I vegetarian?", "must": ["yes|you are vegetarian"], "not": []},
        {"where": "Thesis", "q": "Who is my co-founder, and what is our seed target?", "must": [], "not": ["Arjun", "50 lakh"]},
        # Startup: its own tier, plus the user tier.
        {"where": "Startup", "q": "What is GreenLoop, and who handles sales?", "must": ["recycling", "Arjun"], "not": []},
        {"where": "Startup", "q": "What's our seed round target?", "must": ["50 lakh"], "not": []},
        {"where": "Startup", "q": "When does the beta launch?", "must": [f"{beta.day} October" if beta.month == 10 else f"{beta.day} {beta.strftime('%B')}"], "not": []},
        {"where": "Startup", "q": "Which database did we choose?", "must": ["Postgres(QL)?"], "not": []},
        {"where": "Startup", "q": "When is my thesis deadline?", "must": [], "not": ["15 December"]},
        {"where": "Startup", "q": "Do I like early mornings?", "must": ["no|not|don't|dislike|hate"], "not": []},
    ]


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "nexus-postgres", "psql", "-U", "postgres", "-At", "-F", "\t", "-c", sql],
                          capture_output=True, text=True).stdout


async def settle(user_id: str, timeout: float = 900) -> float:
    """Wait until every gate decision and graph write for the user is done."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        pending = psql(
            f"select (select count(*) from gate_decisions where user_id='{user_id}' and status in ('pending','running'))"
            f" + (select count(*) from memory_episodes e join gate_decisions g on g.id=e.decision_id "
            f"where g.user_id='{user_id}' and e.status='pending')").strip()
        if pending == "0":
            return time.monotonic() - started
        await asyncio.sleep(5)
    return -1


def score(answer: str, must: list[str], forbidden: list[str]) -> tuple[bool, list[str], list[str]]:
    """Each entry is a regular expression, matched as whole words."""
    found = lambda pattern: re.search(rf"\b(?:{pattern})\b", answer, re.IGNORECASE)  # noqa: E731
    missing = [m for m in must if not found(m)]
    leaked = [f for f in forbidden if found(f)]
    return not missing and not leaked, missing, leaked


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args()
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    token = env["SERVICE_TOKEN"]
    today = date.today()

    report: dict = {"date": today.isoformat(), "statements": [], "gate": [], "graph": {}, "answers": []}
    async with httpx.AsyncClient(timeout=300, headers={"Origin": "http://localhost:3000"}) as c:
        user = (await c.post(f"{CORE}/auth/signup", json={
            "email": f"memscen-{uuid.uuid4().hex[:8]}@example.com", "password": uuid.uuid4().hex,
            "display_name": "Meera"})).raise_for_status().json()
        uid = user["id"]
        try:
            projects = {name: (await c.post(f"{CORE}/projects", json={"name": name})).json()["id"]
                        for name in ("Thesis", "Startup")}

            async def new_chat(where: str) -> str:
                if where == "user":
                    return (await c.post(f"{CORE}/chats", json={})).json()["id"]
                return (await c.post(f"{CORE}/projects/{projects[where]}/chats", json={})).json()["id"]

            chats = {where: await new_chat(where) for where in ("user", "Thesis", "Startup")}
            for where, text in STATEMENTS:
                r = (await c.post(f"{CORE}/chats/{chats[where]}/messages", json={"content": text})).raise_for_status().json()
                report["statements"].append({"where": where, "said": text, "reply": r["assistant_message"]["content"]})
                print(f"said [{where}] {text}", flush=True)

            print("waiting for memory to settle…", flush=True)
            report["settle_s"] = round(await settle(uid))
            for row in psql(
                f"select g.user_message, g.persist, g.decided_by, g.facts::text, "
                f"coalesce((select string_agg(e.scope||':'||e.status||coalesce(' '||e.error,''), ',') "
                f"from memory_episodes e where e.decision_id=g.id),'') "
                f"from gate_decisions g where g.user_id='{uid}' order by g.created_at").splitlines():
                said, persist, by, facts, episodes = (row.split("\t") + [""] * 5)[:5]
                report["gate"].append({"said": said, "persist": persist == "t", "by": by,
                                       "facts": json.loads(facts or "[]"), "episodes": episodes})

            # Every ledger fact per tier, retired ones marked: what recall can draw on.
            neo_pw = env["NEO4J_PASSWORD"]
            out = subprocess.run(
                ["docker", "exec", "nexus-neo4j", "cypher-shell", "-u", env.get("NEO4J_USERNAME", "neo4j"), "-p", neo_pw,
                 "--format", "plain",
                 f"MATCH (n:NexusFact) WHERE n.group_id STARTS WITH 'u_{uid.replace('-', '')}' OR n.group_id STARTS WITH "
                 f"'p_{uid.replace('-', '')}' RETURN n.group_id, n.statement, n.nexus_retired_at IS NOT NULL"],
                capture_output=True, text=True).stdout
            report["ledger"] = [line for line in out.splitlines()[1:] if line.strip()]

            mem = httpx.Client(base_url=MEMORY, headers={"X-Service-Token": token}, timeout=60)
            for tier, params in [("user", {"view": "user"}),
                                 *[(name, {"view": "project", "project_id": pid}) for name, pid in projects.items()]]:
                g = mem.get("/graph", params={"user_id": uid, **params}).json()
                names = {n["id"]: n.get("name") or n.get("label") for n in g.get("nodes", [])}
                report["graph"][tier] = {
                    "nodes": sorted(filter(None, names.values())),
                    "edges": [f"{names.get(e.get('source'), '?')} -[{e.get('label') or e.get('name')}]-> "
                              f"{names.get(e.get('target'), '?')}: {e.get('fact', '')}" for e in g.get("edges", [])],
                }

            for item in questions(today):
                chat = await new_chat(item["where"])
                r = (await c.post(f"{CORE}/chats/{chat}/messages", json={"content": item["q"]})).raise_for_status().json()
                answer = r["assistant_message"]["content"]
                ok, missing, leaked = score(answer, item["must"], item["not"])
                report["answers"].append({**item, "answer": answer, "ok": ok, "missing": missing, "leaked": leaked,
                                          "recalled": [f["fact"] for f in r["recalled"]]})
                print(f"{'PASS' if ok else 'FAIL'} [{item['where']}] {item['q']}  -> {answer[:110]!r}"
                      + (f"  missing={missing}" if missing else "") + (f"  leaked={leaked}" if leaked else ""), flush=True)
        finally:
            await _remove_user(c, uid)
            # The graph tiers go with the user only if asked for: clear them.
            try:
                mem = httpx.Client(base_url=MEMORY, headers={"X-Service-Token": token}, timeout=60)
                mem.delete("/graph", params={"user_id": uid, "view": "user"})
                for pid in projects.values():
                    mem.delete("/graph", params={"user_id": uid, "view": "project", "project_id": pid})
            except Exception as exc:
                print("graph cleanup failed:", exc)

    passed = sum(a["ok"] for a in report["answers"])
    report["score"] = f"{passed}/{len(report['answers'])}"
    print(f"\nrecall: {report['score']} answers correct; memory settled in {report['settle_s']}s")
    for tier, g in report["graph"].items():
        print(f"\n[{tier}] {len(g['nodes'])} nodes, {len(g['edges'])} edges")
        for e in g["edges"]:
            print("   ", e[:160])
    if args.out:
        Path(f"{args.out}.json").write_text(json.dumps(report, indent=2))
        lines = [f"# Memory scenarios, {report['date']}", "", f"Recall: **{report['score']}**; memory settled in "
                 f"{report['settle_s']} s.", "", "| Where | Question | Result | Answer |", "|---|---|---|---|"]
        for a in report["answers"]:
            note = "pass" if a["ok"] else "FAIL " + " ".join(
                [f"missing {a['missing']}" if a["missing"] else "", f"leaked {a['leaked']}" if a["leaked"] else ""])
            lines.append(f"| {a['where']} | {a['q']} | {note.strip()} | {a['answer'][:120].replace('|', '/')} |".replace("\n", " "))
        lines += ["", "## What the memory filter kept", "", "| Said | Kept | Facts |", "|---|---|---|"]
        for g in report["gate"]:
            lines.append(f"| {g['said'][:70]} | {g['by']} | {'; '.join(f['statement'] for f in g['facts'])} |")
        lines += ["", "## Graph per tier", ""]
        for tier, g in report["graph"].items():
            lines += [f"**{tier}**: {len(g['nodes'])} nodes, {len(g['edges'])} edges", ""]
            lines += [f"- {e}" for e in g["edges"]] + [""]
        Path(f"{args.out}.md").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
