"""The real thing, on small models: opt-in, because it needs Apple silicon and
downloaded weights.

    RUN_MLX=1 uv run pytest runtime/tests/test_runtime_mlx.py -v

Uses a 0.6B chat model so it runs in seconds; the behaviours under test
(schema-constrained JSON, prompt reuse, embedding shape, unload freeing
memory) do not depend on model size.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_MLX") != "1", reason="set RUN_MLX=1 to run on real MLX models")

CHAT = os.getenv("RUN_MLX_CHAT_MODEL", "mlx-community/Qwen3-0.6B-4bit")
EMBED = os.getenv("RUN_MLX_EMBED_MODEL", "mlx-community/embeddinggemma-300m-4bit")


@pytest.fixture(scope="module")
def backend():
    import asyncio

    from nexus_runtime.config import Settings
    from nexus_runtime.engine import Scheduler
    from nexus_runtime.mlx_backend import MLXBackend

    os.environ.setdefault("SERVICE_TOKEN", "test-service-token")
    settings = Settings()
    settings.chat_model, settings.embed_model = CHAT, EMBED
    scheduler = Scheduler()
    scheduler.start()
    b = MLXBackend(settings, scheduler)
    asyncio.run(b.start())
    yield b
    scheduler.stop()


async def _complete(backend, messages, **params):
    from nexus_runtime.engine import Priority
    from nexus_runtime.mlx_backend import GenParams

    text, done = [], None
    async for event in backend.chat(messages, GenParams(**{"max_tokens": 128, **params}), Priority.LIVE):
        if event.kind == "text":
            text.append(event.text)
        elif event.kind == "error":
            raise AssertionError(event.text)
        else:
            done = event
    return "".join(text), done


async def test_it_generates_text(backend):
    text, done = await _complete(backend, [{"role": "user", "content": "Say the word 'ready'."}])
    assert text.strip()
    assert "<think>" not in text
    assert done.finish_reason in ("stop", "length")


async def test_json_schema_output_is_always_valid(backend):
    schema = {
        "type": "object",
        "properties": {
            "entities": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "kind": {"enum": ["person", "tool"]}},
                "required": ["name", "kind"],
            }},
        },
        "required": ["entities"],
    }
    text, done = await _complete(
        backend,
        [{"role": "user", "content": "Extract entities from: Aarya uses Neo4j and Qdrant."}],
        json_schema=schema, max_tokens=256, temperature=0.0,
    )
    data = json.loads(text)
    assert isinstance(data["entities"], list)
    for entity in data["entities"]:
        assert set(entity) >= {"name", "kind"} and entity["kind"] in ("person", "tool")
    assert done.finish_reason == "stop"


async def test_a_follow_up_reuses_the_conversation_prefix(backend):
    system = {"role": "system", "content": "You are a terse assistant. " * 40}
    first = [system, {"role": "user", "content": "Name a colour."}]
    reply, _ = await _complete(backend, first, temperature=0.0)
    _, done = await _complete(
        backend,
        first + [{"role": "assistant", "content": reply}, {"role": "user", "content": "Another?"}],
        temperature=0.0,
    )
    # At least the long system prompt came from the cache.
    assert done.cached_tokens > 200


async def test_embeddings_are_unit_vectors_of_the_configured_size(backend):
    vectors, used = await backend.embed(["graph databases", "vector search"], None)
    assert len(vectors) == 2 and len(vectors[0]) == 768 and used > 0
    assert abs(sum(x * x for x in vectors[0]) - 1.0) < 1e-3
    short, _ = await backend.embed(["graph databases"], 256)
    assert len(short[0]) == 256 and abs(sum(x * x for x in short[0]) - 1.0) < 1e-3


async def test_unloading_gives_memory_back(backend):
    before = backend.status()["active_memory_gb"]
    await backend.unload("embed")
    after = backend.status()["active_memory_gb"]
    await backend.load("embed")
    assert after < before


async def test_json_properties_come_out_in_schema_order(backend):
    """xgrammar emits properties in the order the schema lists them, and the
    gates rely on that order (decide, then explain)."""
    schema = {"type": "object",
              "properties": {"zeta": {"type": "boolean"}, "alpha": {"type": "string"}},
              "required": ["zeta", "alpha"]}
    text, _ = await _complete(backend, [{"role": "user", "content": "Answer."}],
                              json_schema=schema, temperature=0.0)
    assert list(json.loads(text)) == ["zeta", "alpha"]


async def test_a_reused_prefix_never_leaks_an_earlier_answer(backend):
    """Regression (2026-10-04): the prompt cache was filed under a key one
    token shorter than what it held, so a later request sharing the prefix
    started from a stale token -- "AliceQdrant" for a sentence about Qdrant.
    Every name returned must come from the request's own text."""
    system = {"role": "system", "content": "List every named entity as JSON. " * 30}
    schema = {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}},
              "required": ["names"]}
    sentences = ["Alice Zephyrine met Bob Quartermain in Oslo.", "Qdrant holds the vectors for Nexus.",
                 "Siya uses PaddleOCR.", "Alice Zephyrine met Bob Quartermain in Oslo."]
    for sentence in sentences:
        text, done = await _complete(backend, [system, {"role": "user", "content": sentence}],
                                     json_schema=schema, temperature=0.0)
        for name in json.loads(text)["names"]:
            assert name in sentence, (name, sentence)
    assert done.cached_tokens > 0  # the prefix really was reused
