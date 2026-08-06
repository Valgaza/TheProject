#!/usr/bin/env python3
"""
Seed the Nexus knowledge graph with demo data, then print live demo prompts.

Usage
-----
    # Seed the graph (waits ~30 s between messages for graphiti to finish)
    python seed_graph.py

    # Skip seeding — just print the live demo prompts
    python seed_graph.py --prompts

    # Point at a different service
    python seed_graph.py --url http://localhost:8000
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

MEMORY_URL = "http://localhost:8000"
USER_ID    = "user_001"

# ── Seed data ──────────────────────────────────────────────────────────────────
# Six messages that build a coherent graph before the demo starts.
# Graphiti extracts entities (people, orgs, projects) and their relationships.

SEED_MESSAGES = [
    # People + org
    "Aria Patel is the CEO of Nexus Labs, a company building local-first AI infrastructure.",
    "Marcus Webb is the CTO at Nexus Labs. He leads the engineering division and reports to Aria Patel.",
    # Projects
    "The Prism project is a distributed caching layer for the inference engine. It is led by David Park.",
    "David Park is a senior engineer at Nexus Labs on Marcus Webb's team. He is the technical lead for Prism.",
    # Infrastructure + geography
    "The Atlas system is Nexus Labs' core inference engine. It handles routing, retrieval, and model dispatch.",
    "Nexus Labs is headquartered in Berlin. The research division operates out of the Bengaluru office.",
]

# ── Live demo prompts ──────────────────────────────────────────────────────────
# Type these one at a time at http://localhost:3000/demo to watch the graph grow.

DEMO_PROMPTS = [
    (
        "Add a new person",
        "Lena Zhao joined Nexus Labs as Head of Research last month, based out of the Bengaluru office.",
    ),
    (
        "Connect two projects",
        "The Prism caching layer integrates directly with the Atlas inference engine to reduce end-to-end latency by 40%.",
    ),
    (
        "Add a collaboration",
        "David Park and Lena Zhao are co-authoring a technical paper on knowledge graph construction for enterprise AI.",
    ),
    (
        "Add a business event",
        "Nexus Labs announced a Series B funding round led by Horizon Ventures, valuing the company at 120 million dollars.",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────


def post_ingest(text: str, url: str) -> bool:
    payload = json.dumps({"text": text, "user_id": USER_ID}).encode()
    req = urllib.request.Request(
        f"{url}/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 202
    except urllib.error.URLError as e:
        print(f"       ✗  request failed: {e}")
        return False


def health_check(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
            data = json.loads(resp.read())
            neo4j = data.get("neo4j", "unknown")
            if neo4j == "ok":
                print(f"   ✓  service healthy  (neo4j: ok)")
                return True
            else:
                print(f"   ⚠  neo4j status: {neo4j} — graph writes may fail")
                return True  # still try
    except Exception as e:
        print(f"   ✗  cannot reach memory service at {url}: {e}")
        return False


def print_prompts() -> None:
    print()
    print("── Live demo prompts ────────────────────────────────────────────")
    print("   Open http://localhost:3000/demo and paste these one at a time.")
    print()
    for i, (label, prompt) in enumerate(DEMO_PROMPTS, 1):
        print(f"   {i}. [{label}]")
        print(f"      {prompt}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Nexus demo graph.")
    parser.add_argument("--prompts", action="store_true",
                        help="Print live demo prompts only, skip seeding")
    parser.add_argument("--url", default=MEMORY_URL,
                        help=f"Memory service base URL (default: {MEMORY_URL})")
    args = parser.parse_args()

    if args.prompts:
        print_prompts()
        return

    url = args.url.rstrip("/")

    print()
    print("── Nexus graph seeder ───────────────────────────────────────────")
    print(f"   Service : {url}")
    print(f"   User    : {USER_ID}")
    print(f"   Messages: {len(SEED_MESSAGES)}")
    print()

    if not health_check(url):
        print()
        print("   Start the memory service first:")
        print("     ./start.sh")
        sys.exit(1)

    print()

    WAIT_SECS = 32  # graphiti + OpenAI typically takes 15–30 s per episode

    for i, msg in enumerate(SEED_MESSAGES, 1):
        preview = msg[:80] + "…" if len(msg) > 80 else msg
        print(f"   [{i}/{len(SEED_MESSAGES)}]  {preview}")

        ok = post_ingest(msg, url)
        if not ok:
            print("          → failed — check memory service logs and continue manually")
            continue

        print(f"          → accepted", end="")

        if i < len(SEED_MESSAGES):
            print(f", waiting {WAIT_SECS}s ", end="", flush=True)
            for _ in range(WAIT_SECS):
                time.sleep(1)
                print("·", end="", flush=True)
        print()

    print()
    print("── Seeding complete ─────────────────────────────────────────────")
    print("   Open http://localhost:3000/graph — you should see ~10 nodes.")
    print("   If the graph is still empty, wait 30 s and refresh.")
    print_prompts()


if __name__ == "__main__":
    main()
