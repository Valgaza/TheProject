"""Rehearse the doctor demo (docs/doctor-demo.md) against a throwaway copy.

Seeds a fresh account exactly as scripts/seed_doctor_demo.py does, then sends
every typed line of the presenter's script in order, in the same chats, and
checks each answer for what it must and must not say. Deletes the account at
the end.

    uv run python scripts/doctor_demo_check.py [--runs 2]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from conftest import _remove_user  # noqa: E402
from seed_doctor_demo import CORE, DOCTOR, MEMORY, seed  # noqa: E402

UNKNOWN = r"don't know|do not know|not sure|haven't|have not|no information|not mentioned|no record|can't find|cannot find|not available"


def steps(today: date) -> list[dict]:
    review = date(2026, 10, 27)
    days = (review - today).days
    # chat: which chat the line goes in ("Clinic:A" = a chat in Clinic, kept
    # for the lines that follow it). web: the globe button. settle: wait for
    # memory to finish writing before the next line.
    return [
        {"id": "1.1", "chat": "Clinic:A", "say": "What was the treatment that worked for my last patient with a fungal infection?",
         "must": ["itraconazole", "P-1058"], "not": ["flucloxacillin", "doxycycline"]},
        {"id": "1.2", "chat": "Clinic:A", "say": "And what had been tried for her before that?",
         "must": ["terbinafine"], "not": []},
        {"id": "1.3", "chat": "Clinic:B", "say": "Which of my patients with a fungal infection has a liver condition?",
         "must": ["P-1044", "fatty liver"], "not": []},
        {"id": "2.1", "chat": "Clinic:C", "say": "According to our protocol, what is the first-line treatment for a localised tinea infection?",
         "must": ["terbinafine", "clotrimazole"], "not": []},
        {"id": "2.2", "chat": "Clinic:C", "say": "What does the protocol say I must check before starting oral terbinafine?",
         "must": ["liver"], "not": []},
        {"id": "2.3", "chat": "Clinic:C", "say": "How long do fungal culture results take?",
         "must": ["3 weeks|three weeks"], "not": []},
        {"id": "3.1", "chat": "Clinic:D", "say": "Patient P-1044 needs oral terbinafine again. What should I check before I start it?",
         "must": ["liver|LFT"], "not": []},
        {"id": "4.1", "chat": "Clinic:E", "settle": True,
         "say": "New patient P-1063: a 34-year-old woman with tinea corporis on her forearm. I've started her on terbinafine 1% cream once daily for 2 weeks, with a review on 27 October 2026.",
         "must": [], "not": []},
        {"id": "4.2", "chat": "Clinic:F", "say": "What is patient P-1063 using, and when is her review?",
         "must": ["terbinafine", "27 October"], "not": []},
        {"id": "4.3", "chat": "Clinic:F", "say": "How many days is that from today?",
         "must": [str(days)], "not": []},
        {"id": "4.4", "chat": "Clinic:G", "settle": True,
         "say": "Update on patient P-1063: the terbinafine cream irritated her skin, so I switched her to clotrimazole 1% cream twice daily.",
         "must": [], "not": []},
        {"id": "4.5", "chat": "Clinic:H", "say": "What is patient P-1063 using now?",
         "must": ["clotrimazole"], "not": ["using terbinafine", "on terbinafine 1% cream once daily"]},
        {"id": "5.1", "chat": "Conference talk:A", "say": "When and where is my talk, and what will it cover?",
         "must": ["21 November", "Pune Dermatology Meet", "indotineae"], "not": []},
        {"id": "5.2", "chat": "Conference talk:A", "say": "What is patient P-1063 using now?",
         "must": [UNKNOWN], "not": ["clotrimazole", "terbinafine 1% cream"]},
        {"id": "5.3", "chat": "loose:A", "say": "Where do I work, and what is my specialty?",
         "must": ["Lakeview", "dermatolog"], "not": []},
        {"id": "6.1", "chat": "Clinic:I", "web": True,
         "say": "Search the web for the latest treatment guidance on terbinafine-resistant Trichophyton indotineae for my patient Asha Menon, MRN 00482913.",
         "must": ["itraconazole|indotineae|resistan"], "not": [], "query_not": ["Asha", "Menon", "00482913"]},
    ]


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "nexus-postgres", "psql", "-U", "postgres", "-At", "-c", sql],
                          capture_output=True, text=True).stdout.strip()


async def settle(user_id: str, timeout: float = 300) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        pending = psql(
            f"select (select count(*) from gate_decisions where user_id='{user_id}' and status in ('pending','running'))"
            f" + (select count(*) from memory_episodes e join gate_decisions g on g.id=e.decision_id "
            f"where g.user_id='{user_id}' and e.status='pending')")
        if pending == "0":
            return
        await asyncio.sleep(3)


def found(pattern: str, text: str) -> bool:
    return bool(re.search(rf"(?:{pattern})", text, re.IGNORECASE))


async def rehearse() -> int:
    token = dotenv_values(ROOT / ".env")["SERVICE_TOKEN"]
    failures = 0
    async with httpx.AsyncClient(timeout=300, headers={"Origin": "http://localhost:3000"}) as c:
        user = (await c.post(f"{CORE}/auth/signup", json={
            "email": f"doctor-check-{uuid.uuid4().hex[:8]}@example.com", "password": uuid.uuid4().hex,
            "display_name": DOCTOR})).raise_for_status().json()
        projects: dict[str, str] = {}
        try:
            projects = await seed(c, user["id"], token)
            print("  waiting 40 s for the documents' own memory writes…", flush=True)
            await asyncio.sleep(40)
            chats: dict[str, str] = {}
            for step in steps(date.today()):
                where, _ = step["chat"].split(":")
                if step["chat"] not in chats:
                    url = f"{CORE}/chats" if where == "loose" else f"{CORE}/projects/{projects[where]}/chats"
                    chats[step["chat"]] = (await c.post(url, json={})).raise_for_status().json()["id"]
                r = (await c.post(f"{CORE}/chats/{chats[step['chat']]}/messages",
                                  json={"content": step["say"], "use_web": step.get("web", False)})).raise_for_status().json()
                answer = r["assistant_message"]["content"]
                query = ((r["assistant_message"]["metadata"].get("web") or {}).get("query_sent")) or ""
                missing = [m for m in step["must"] if not found(m, answer)]
                leaked = [n for n in step["not"] if found(n, answer)]
                leaked += [f"query:{q}" for q in step.get("query_not", []) if q.lower() in query.lower()]
                ok = not missing and not leaked
                failures += not ok
                print(f"{'PASS' if ok else 'FAIL'} {step['id']} [{r['route']['handler']}] {step['say'][:70]}", flush=True)
                print(f"      -> {' '.join(answer.split())[:220]}" + (f"\n      query: {query}" if query else "")
                      + (f"\n      missing {missing}" if missing else "") + (f"\n      leaked {leaked}" if leaked else ""), flush=True)
                if step.get("settle"):
                    await settle(user["id"])
        finally:
            await _remove_user(c, user["id"])
            async with httpx.AsyncClient(base_url=MEMORY, headers={"X-Service-Token": token}, timeout=60) as m:
                await asyncio.sleep(10)
                await m.delete("/graph", params={"user_id": user["id"], "view": "user"})
                for pid in projects.values():
                    await m.delete("/graph", params={"user_id": user["id"], "view": "project", "project_id": pid})
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    total = 0
    for run in range(args.runs):
        print(f"\n=== rehearsal {run + 1} of {args.runs}")
        failed = await rehearse()
        total += failed
        print(f"=== {len(steps(date.today())) - failed}/{len(steps(date.today()))} passed")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
