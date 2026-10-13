"""Set up the doctor demo: a fictional dermatologist with a history of patients.

Creates (or resets) the account below, two projects, uploads the clinic's two
documents into "Clinic", and writes the patients seen over the past months
into memory, each fact dated the day it happened, so "my last patient with a
fungal infection" has one answer. Everything here is invented: the doctor, the
clinic, the patients (case numbers only, no names).

    uv run python scripts/seed_doctor_demo.py            # the demo account
    uv run python scripts/seed_doctor_demo.py --reset    # delete it first, then seed again

The presenter's script is docs/doctor-demo.md; scripts/doctor_demo_check.py
rehearses it against a throwaway copy of this seed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
CORE = os.getenv("CORE_URL", "http://localhost:8100")
MEMORY = os.getenv("MEMORY_URL", "http://localhost:8000")

DOCTOR = "Dr. Neha Kulkarni"
EMAIL = "dr.kulkarni@lakeview.example"
PASSWORD = "nexus-doctor-demo"  # a demo fixture on a local install, like db/seed.sql's
DOCS = [ROOT / "rag/sample_docs/lakeview-antifungal-protocol.md",
        ROOT / "rag/sample_docs/lakeview-clinic-handbook.md"]


def _day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(hour=10, tzinfo=timezone.utc)


# (tier, date, [(statement, subject, relation, object)]). One entry is one
# write; the date is when it happened.
HISTORY: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    ("user", "2026-06-01", [
        (f"{DOCTOR} is a dermatologist at Lakeview Skin Clinic in Pune.", DOCTOR, "WORKS_AT", "Lakeview Skin Clinic"),
        (f"{DOCTOR} prefers answers that give the drug, the dose and the duration.",
         DOCTOR, "PREFERS", "drug, dose and duration"),
    ]),
    ("Clinic", "2026-06-12", [
        (f"On 12 June 2026, {DOCTOR} saw patient P-1031, a 29-year-old man with tinea cruris, a fungal infection of the groin.",
         "P-1031", "HAD", "tinea cruris"),
    ]),
    ("Clinic", "2026-07-10", [
        ("For patient P-1031, clotrimazole 1% cream twice daily for 4 weeks worked: his tinea cruris had cleared at the review on 10 July 2026.",
         "P-1031", "WAS_CURED_BY", "clotrimazole 1% cream"),
    ]),
    ("Clinic", "2026-08-04", [
        (f"On 4 August 2026, {DOCTOR} saw patient P-1044, a 52-year-old woman with extensive tinea corporis, a fungal infection on her back.",
         "P-1044", "HAD", "tinea corporis"),
        ("Patient P-1044 has fatty liver disease; her baseline liver function tests on 4 August 2026 were normal.",
         "P-1044", "HAS_CONDITION", "fatty liver disease"),
    ]),
    ("Clinic", "2026-08-20", [
        (f"On 20 August 2026, {DOCTOR} saw patient P-1050, a 36-year-old man with a flare of hand eczema, which is not an infection.",
         "P-1050", "HAD", "hand eczema"),
        ("For patient P-1050, mometasone 0.1% cream once daily for 2 weeks settled his hand eczema.",
         "P-1050", "WAS_TREATED_WITH", "mometasone 0.1% cream"),
    ]),
    ("Clinic", "2026-08-25", [
        ("For patient P-1044, oral terbinafine 250 mg once daily for 3 weeks worked: her tinea corporis had cleared at the review on 25 August 2026.",
         "P-1044", "WAS_CURED_BY", "oral terbinafine 250 mg"),
    ]),
    ("Clinic", "2026-09-03", [
        (f"On 3 September 2026, {DOCTOR} saw patient P-1053, a 61-year-old man with cellulitis of the left lower leg, a bacterial infection.",
         "P-1053", "HAD", "cellulitis"),
        ("For patient P-1053, oral flucloxacillin 500 mg four times daily for 7 days cleared his cellulitis.",
         "P-1053", "WAS_CURED_BY", "oral flucloxacillin 500 mg"),
    ]),
    ("Clinic", "2026-09-22", [
        (f"On 22 September 2026, {DOCTOR} saw patient P-1058, a 41-year-old woman with tinea corporis, a fungal infection on her forearm.",
         "P-1058", "HAD", "tinea corporis"),
        ("For patient P-1058, terbinafine 1% cream once daily for 2 weeks, started on 8 September 2026, had not worked.",
         "P-1058", "WAS_NOT_HELPED_BY", "terbinafine 1% cream"),
    ]),
    ("Clinic", "2026-10-01", [
        (f"On 1 October 2026, {DOCTOR} saw patient P-1060, a 24-year-old woman with moderate acne.",
         "P-1060", "HAD", "moderate acne"),
        (f"For patient P-1060, {DOCTOR} started oral doxycycline 100 mg once daily for 12 weeks.",
         "P-1060", "WAS_STARTED_ON", "oral doxycycline 100 mg"),
    ]),
    ("Clinic", "2026-10-06", [
        ("For patient P-1058, oral itraconazole 100 mg twice daily for 2 weeks worked: her tinea corporis had cleared at the review on 6 October 2026.",
         "P-1058", "WAS_CURED_BY", "oral itraconazole 100 mg"),
    ]),
    ("Conference talk", "2026-10-05", [
        (f"{DOCTOR} is giving a 15-minute talk on antifungal resistance at the Pune Dermatology Meet on 21 November 2026.",
         DOCTOR, "IS_GIVING_A_TALK_AT", "Pune Dermatology Meet"),
        ("The talk at the Pune Dermatology Meet will cover the rise of terbinafine-resistant Trichophyton indotineae.",
         "the talk", "COVERS", "terbinafine-resistant Trichophyton indotineae"),
    ]),
]


async def seed(c: httpx.AsyncClient, user_id: str, token: str) -> dict[str, str]:
    """Projects, documents and history for the signed-in user. Returns the
    project ids by name."""
    projects = {}
    for name, description in [("Clinic", "Patients seen at Lakeview Skin Clinic"),
                              ("Conference talk", "Talk at the Pune Dermatology Meet")]:
        projects[name] = (await c.post(f"{CORE}/projects", json={"name": name, "description": description,
                                                                 "domain": "general"})).raise_for_status().json()["id"]
    for path in DOCS:
        doc = (await c.post(f"{CORE}/documents?project_id={projects['Clinic']}",
                            files={"file": (path.name, path.read_bytes(), "text/markdown")})).raise_for_status().json()
        print(f"  document {path.name}: {doc.get('status')}, {doc.get('chunk_count')} chunks", flush=True)

    async with httpx.AsyncClient(base_url=MEMORY, headers={"X-Service-Token": token}, timeout=120) as m:
        for tier, day, facts in HISTORY:
            body = {
                "text": "\n".join(f[0] for f in facts),
                "user_id": user_id,
                "project_id": projects.get(tier),
                "scope": "user" if tier == "user" else "project",
                "wait": True,
                "occurred_at": _day(day).isoformat(),
                "facts": [{"statement": s, "subject": su, "relation": r, "object": o} for s, su, r, o in facts],
            }
            r = await m.post("/ingest", json=body)
            r.raise_for_status()
            print(f"  memory [{tier}] {day}: {len(facts)} fact(s)", flush=True)
    return projects


async def _delete_everything(c: httpx.AsyncClient) -> None:
    for project in (await c.get(f"{CORE}/projects")).json():
        await c.delete(f"{CORE}/graph", params={"view": "project", "project_id": project["id"]})
    await c.delete(f"{CORE}/graph", params={"view": "user"})
    for doc in (await c.get(f"{CORE}/documents")).json():
        await c.delete(f"{CORE}/documents/{doc['id']}")
    for chat in (await c.get(f"{CORE}/chats")).json():
        await c.delete(f"{CORE}/chats/{chat['id']}")
    for project in (await c.get(f"{CORE}/projects")).json():
        await c.delete(f"{CORE}/projects/{project['id']}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete the account's data first")
    args = parser.parse_args()
    token = dotenv_values(ROOT / ".env")["SERVICE_TOKEN"]
    async with httpx.AsyncClient(timeout=300, headers={"Origin": "http://localhost:3000"}) as c:
        r = await c.post(f"{CORE}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code != 200:
            r = await c.post(f"{CORE}/auth/signup", json={"email": EMAIL, "password": PASSWORD, "display_name": DOCTOR})
            r.raise_for_status()
            print(f"created {EMAIL}")
        user = (await c.get(f"{CORE}/auth/me")).json()
        if (await c.get(f"{CORE}/projects")).json():
            if not args.reset:
                print("The account already has projects. Run with --reset to start again.")
                return 1
            await _delete_everything(c)
            time.sleep(2)
        await seed(c, user["id"], token)
    print(f"\nReady. Sign in as {EMAIL} / {PASSWORD}. Give the documents' own memory writes ~30 s.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
