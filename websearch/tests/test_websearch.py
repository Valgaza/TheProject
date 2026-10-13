"""The one path where text leaves the machine.

Flag 1 in docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md is why these exist. A test
query like "AI news" never contains anything sensitive, so it can never catch
a leak; these use the doctor's query the flag was written about.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nexus_websearch.config import Settings
from nexus_websearch.main import SearchRequest, Searcher, app
from nexus_websearch.redaction import rewrite_messages, strip_identifiers

DOCTOR = "search the internet for Priya Nair's stage 3 lymphoma options, MRN 00482913"


class World:
    """The runtime and SearXNG, faked at the HTTP layer."""

    def __init__(self, rewrite: str | None = "stage 3 lymphoma treatment options",
                 results_by_engine: dict | None = None, identifies: float = 0.05,
                 searxng_down: bool = False) -> None:
        self.rewrite = rewrite
        self.results_by_engine = results_by_engine or {None: [
            {"title": "Lymphoma treatment", "url": "https://example.org/a", "content": "..."}]}
        self.identifies = identifies
        self.searxng_down = searxng_down
        self.sent: list[str] = []
        self.engines: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            if self.rewrite is None:
                return httpx.Response(500, json={"detail": "model down"})
            return httpx.Response(200, json={"choices": [{"message": {"content": self.rewrite}}]})
        if request.url.path == "/decide":
            return httpx.Response(200, json={"answers": {"identifies_someone": {
                "probs": {"identifies": self.identifies, "generic": 1 - self.identifies}}}})
        if request.url.path == "/search":
            if self.searxng_down:
                raise httpx.ConnectError("searxng down", request=request)
            self.sent.append(request.url.params["q"])
            engine = request.url.params.get("engines")
            self.engines.append(engine)
            return httpx.Response(200, json={"results": self.results_by_engine.get(engine, [])})
        return httpx.Response(404)


def _searcher(world: World, tmp_path, **overrides) -> Searcher:
    s = Settings()
    s.searxng_url, s.runtime_url = "http://searxng.test", "http://runtime.test"
    s.audit_log = str(tmp_path / "audit.jsonl")
    s.laya_query = overrides.pop("laya_query", "shadow")
    for k, v in overrides.items():
        setattr(s, k, v)
    return Searcher(s, httpx.AsyncClient(transport=httpx.MockTransport(world.handler)))


def _audit(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ── The deterministic layer ──────────────────────────────────────────────────


def test_the_person_s_own_name_is_stripped_whole_or_in_parts():
    query, removed = strip_identifiers("Aarya's thesis on raft", ["Aarya Patil"])
    assert query == "thesis on raft"
    assert removed == ["Aarya's"]


def test_emails_and_long_numbers_are_stripped():
    query, _ = strip_identifiers("case 2024-CV-118273 filed by x@firm.com lymphoma MRN00482913", [])
    assert "118273" not in query and "@" not in query and "00482913" not in query
    assert "lymphoma" in query


def test_ordinary_numbers_in_topics_survive():
    query, removed = strip_identifiers("COVID-19 guidance 2024 for GPT-4", [])
    assert query == "COVID-19 guidance 2024 for GPT-4"
    assert removed == []


def test_the_rewrite_sees_earlier_messages_and_today_s_date():
    from datetime import date

    system, user = rewrite_messages(
        "Check it online and get me this year's results.",
        ["hi", "What happened at the latest Singapore Grand Prix?"], date(2026, 10, 12))
    assert "12 October 2026" in system["content"]
    assert user["content"].endswith("Message: Check it online and get me this year's results.")
    assert "- What happened at the latest Singapore Grand Prix?" in user["content"]
    # On its own, a message goes as it always did.
    assert rewrite_messages("raft", [], date(2026, 10, 12))[1]["content"] == "raft"


# ── A search ─────────────────────────────────────────────────────────────────


async def test_what_leaves_is_the_rewrite_not_the_message(tmp_path):
    world = World()
    result = await _searcher(world, tmp_path).search(SearchRequest(message=DOCTOR))

    assert world.sent == ["stage 3 lymphoma treatment options"]
    assert result.query_sent == "stage 3 lymphoma treatment options"
    assert result.results[0].url == "https://example.org/a"


async def test_a_rewrite_that_leaks_the_person_s_name_is_still_caught(tmp_path):
    world = World(rewrite="Aarya Patil raft consensus")
    await _searcher(world, tmp_path).search(
        SearchRequest(message="what did I say about raft", known_names=["Aarya Patil", "aarya@example.com"]))
    assert world.sent == ["raft consensus"]


@pytest.mark.parametrize("rewrite", [None, "", "   "])
async def test_no_safe_query_means_nothing_is_sent(rewrite, tmp_path):
    """The raw message is never the fallback."""
    world = World(rewrite=rewrite)
    result = await _searcher(world, tmp_path).search(SearchRequest(message=DOCTOR))
    assert world.sent == []
    assert result.query_sent is None and result.error
    assert _audit(tmp_path) == []  # nothing left, so nothing to record


async def test_the_audit_log_holds_what_left_and_never_the_message(tmp_path):
    world = World()
    await _searcher(world, tmp_path).search(SearchRequest(message=DOCTOR, user_id="u1"))

    entry = _audit(tmp_path)[-1]
    assert entry["query_sent"] == "stage 3 lymphoma treatment options"
    assert entry["user_id"] == "u1"
    text = json.dumps(entry)
    assert "Priya" not in text and "00482913" not in text


async def test_a_live_check_blocks_a_query_that_still_identifies_someone(tmp_path):
    world = World(rewrite="Priya Nair lymphoma", identifies=0.97)
    result = await _searcher(world, tmp_path, laya_query="live").search(SearchRequest(message=DOCTOR))

    assert world.sent == []
    assert result.blocked and result.query_sent is None
    entry = _audit(tmp_path)[-1]
    assert entry["blocked"] and entry["query_sent"] is None and entry["identifies_p"] == 0.97


async def test_a_shadow_check_is_recorded_and_ignored(tmp_path):
    world = World(rewrite="Priya Nair lymphoma", identifies=0.97)
    result = await _searcher(world, tmp_path, laya_query="shadow").search(SearchRequest(message=DOCTOR))
    assert world.sent == ["Priya Nair lymphoma"] and not result.blocked
    assert _audit(tmp_path)[-1]["identifies_p"] == 0.97


async def test_an_empty_answer_falls_back_to_other_engines(tmp_path):
    world = World(results_by_engine={None: [], "duckduckgo,brave": [
        {"title": "T", "url": "https://x", "content": "s"}]})
    result = await _searcher(world, tmp_path).search(SearchRequest(message="raft news"))
    assert world.engines == [None, "duckduckgo,brave"]
    assert result.results[0].url == "https://x"


async def test_a_dead_searxng_is_an_error_not_an_exception(tmp_path):
    world = World(searxng_down=True)
    result = await _searcher(world, tmp_path).search(SearchRequest(message="raft news"))
    assert result.results == [] and "ConnectError" in result.error
    assert result.query_sent == "stage 3 lymphoma treatment options"


async def test_a_repeated_query_is_served_from_the_cache(tmp_path):
    world = World()
    searcher = _searcher(world, tmp_path)
    await searcher.search(SearchRequest(message="a"))
    await searcher.search(SearchRequest(message="b"))  # same rewrite
    assert len(world.sent) == 1


async def test_searches_are_rate_limited_per_person(tmp_path):
    world = World()
    searcher = _searcher(world, tmp_path, rate_per_minute=2)
    for _ in range(2):
        assert not (await searcher.search(SearchRequest(message="x", user_id="u"))).error
    third = await searcher.search(SearchRequest(message="x", user_id="u"))
    assert "too many" in third.error


# ── The service boundary ─────────────────────────────────────────────────────


async def test_only_core_may_search_and_health_stays_open(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "tok")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/search", json={"message": "x"})).status_code == 401
        assert (await c.get("/audit", headers={"X-Service-Token": "wrong"})).status_code == 401
