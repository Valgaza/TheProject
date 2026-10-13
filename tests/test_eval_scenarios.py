"""End-to-end scenarios for the eval: documents, the web, and voice.

    RUN_SCENARIOS=1 uv run pytest tests/test_eval_scenarios.py -v -s

Each drives the live stack as a person would. The web scenario needs the
internet only for SearXNG's results; what it checks (that nothing
identifying left the machine) holds either way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from conftest import CORE_URL, ENV, RUNTIME_URL, WEBSEARCH_URL

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "rag" / "sample_docs" / "kestrel-point.md"
SERVICE = {"X-Service-Token": ENV.get("SERVICE_TOKEN", "")}


async def _project_with_document(client: httpx.AsyncClient) -> tuple[str, str]:
    project = (await client.post(f"{CORE_URL}/projects", json={"name": "[test] eval"})).json()["id"]
    with DOC.open("rb") as f:
        doc = (await client.post(f"{CORE_URL}/documents", params={"project_id": project},
                                 files={"file": (DOC.name, f, "text/markdown")}, timeout=600)).json()
    assert doc["status"] == "ingested" and doc["chunk_count"] > 0, doc
    chat = (await client.post(f"{CORE_URL}/projects/{project}/chats", json={})).json()["id"]
    return project, chat


async def test_a_document_question_is_answered_from_the_right_file(client):
    _, chat = await _project_with_document(client)
    turn = (await client.post(f"{CORE_URL}/chats/{chat}/messages",
                              json={"content": "How many steps are there to the lamp room?"})).json()

    assert "197" in turn["assistant_message"]["content"], turn["assistant_message"]["content"]
    assert DOC.name in {s["file_name"] for s in turn["rag_sources"]}, turn["rag_sources"]


async def test_a_web_search_never_sends_the_identifying_details(client):
    """Flag 1: a doctor's query about a named patient. What left the machine,
    on the message and in the websearch audit log, carries neither the name
    nor the record number."""
    chat = (await client.post(f"{CORE_URL}/chats", json={})).json()["id"]
    message = "Search the web for Priya Nair's stage 3 lymphoma options, MRN 00482913"
    turn = (await client.post(f"{CORE_URL}/chats/{chat}/messages",
                              json={"content": message, "use_web": True})).json()
    web = turn["assistant_message"]["metadata"].get("web") or {}
    sent = web.get("query_sent") or ""
    for secret in ("priya", "nair", "00482913"):
        assert secret not in sent.lower(), sent

    async with httpx.AsyncClient() as c:
        entries = (await c.get(f"{WEBSEARCH_URL}/audit", headers=SERVICE, params={"limit": 200})).json()["entries"]
    text = json.dumps(entries).lower()
    assert "priya" not in text and "00482913" not in text
    if sent:
        assert any(e.get("query_sent") == sent for e in entries), entries[-3:]


async def test_a_spoken_question_is_heard_answered_aloud_and_stored(client):
    """The voice loopback: the runtime's own TTS asks a question, sent in real
    time as a WebRTC microphone track, as the browser would."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from voice_bench import Browser, synthesise

    _, chat = await _project_with_document(client)
    async with httpx.AsyncClient(base_url=RUNTIME_URL, headers=SERVICE) as runtime:
        question = await synthesise(runtime, "How many steps are there to the lamp room?")

    browser = await Browser.connect(client, chat, core=CORE_URL)
    try:
        await browser.until("bot-ready", timeout=600)
        browser.say(question)
        spoke_end = await browser.said()
        transcript_at, transcript = await browser.until("transcript")
        first_at, _ = await browser.until("audio", after=spoke_end)
        latency = first_at - spoke_end
        _, done = await browser.until("done", after=transcript_at, timeout=120)
    finally:
        await browser.close()

    assert "lamp room" in transcript["text"].lower(), transcript
    stored = done["assistant_message"]
    assert "197" in stored["content"], stored["content"]
    assert stored["metadata"]["voice"]["first_audio_ms"] > 0
    # A sanity bound, not the target: scripts/voice_bench.py measures latency
    # over twenty exchanges. This first turn follows an upload whose
    # background cataloguing may still be running.
    assert latency < 5.0, f"first audio {latency:.2f}s after the question ended"

    messages = (await client.get(f"{CORE_URL}/chats/{chat}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
