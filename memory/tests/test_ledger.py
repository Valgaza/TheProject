"""The fact ledger: every accepted fact stored as written, found by meaning."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from nexus_memory import hygiene, ledger
from nexus_memory import main as memory_main
from nexus_memory.main import app

WORDS = ["vegetarian", "peanut", "diet", "food", "deadline", "december", "thesis", "postgres", "database", "sister"]


class WordEmbedder:
    """Vectors from word counts, with "diet" and "food" pulled towards
    "vegetarian": enough meaning for the ranking to be tested."""

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        v = [float(low.count(w)) for w in WORDS]
        if "vegetarian" in low or "peanut" in low:
            v[WORDS.index("diet")] += 1
        return v

    async def create(self, input_data):
        return self._vec(input_data[0] if isinstance(input_data, list) else input_data)

    async def create_batch(self, texts):
        return [self._vec(t) for t in texts]


class LedgerDriver:
    def __init__(self):
        self.facts: dict[tuple[str, str], dict] = {}

    async def execute_query(self, query, **params):
        if "MERGE (n:NexusFact" in query:
            out = []
            for f in params["facts"]:
                row = self.facts.setdefault((f["group_id"], f["key"]), {
                    "uuid": f["uuid"], "group_id": f["group_id"], "statement": f["statement"],
                    "embedding": f["embedding"], "created_at": datetime.now(timezone.utc), "retired": False})
                out.append({"uuid": row["uuid"]})
            return out, None, None
        if "MATCH (n:NexusFact)" in query:
            return [r for r in self.facts.values() if r["group_id"] in params["groups"] and not r["retired"]], None, None
        return [], None, None


@pytest.fixture
def driver():
    return LedgerDriver()


async def test_the_same_fact_is_stored_once_per_tier(driver):
    emb = WordEmbedder()
    await ledger.write(driver, emb, "u_a", ["Meera is vegetarian.", "meera is  vegetarian"])
    await ledger.write(driver, emb, "p_a_b", ["Meera is vegetarian."])
    assert len(driver.facts) == 2


async def test_a_fact_is_found_by_meaning_not_only_by_its_words(driver):
    """"Dietary restrictions" shares no word with "vegetarian"; the old
    keyword-only episode search could never connect them."""
    emb = WordEmbedder()
    await ledger.write(driver, emb, "u_a", [
        "Meera is vegetarian.", "Meera is allergic to peanuts.", "Kavya is Meera's sister."])
    found = await ledger.search(driver, emb, "Any diet or food restrictions?", ["u_a"], limit=5, min_score=0.3)
    assert {f.statement for f in found} == {"Meera is vegetarian.", "Meera is allergic to peanuts."}


async def test_search_stays_inside_the_tiers_it_is_given(driver):
    emb = WordEmbedder()
    await ledger.write(driver, emb, "p_a_thesis", ["The thesis deadline is 15 December."])
    await ledger.write(driver, emb, "p_a_startup", ["The startup database is Postgres."])
    found = await ledger.search(driver, emb, "thesis deadline", ["u_a", "p_a_startup"], limit=5, min_score=0.1)
    assert all(f.group_id != "p_a_thesis" for f in found)


async def test_search_returns_ledger_facts_first(driver, monkeypatch):
    emb = WordEmbedder()
    await ledger.write(driver, emb, "u_00000000000000000000000000000001", ["The thesis deadline is 15 December."])

    class Graphiti:
        def __init__(self):
            self.driver, self.embedder = driver, emb

        async def search_(self, *, query, config, group_ids):
            return type("R", (), {"edges": [], "nodes": [], "episodes": []})()

    monkeypatch.setattr(memory_main, "_graphiti", Graphiti())
    hygiene._retired.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"X-Service-Token": "test-service-token"}) as c:
        body = (await c.post("/search", json={
            "query": "When is the thesis deadline?", "user_id": "00000000-0000-0000-0000-000000000001"})).json()
    assert body["facts"][0]["fact"] == "The thesis deadline is 15 December."
    assert body["facts"][0]["kind"] == "fact"
