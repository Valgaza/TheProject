"""The OpenAI-compatible surface, against a fake backend.

These pin the shapes core's OpenAI SDK and graphiti parse, and the auth that
keeps everything but core and memory out.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nexus_runtime.api import router
from nexus_runtime.engine import Priority
from nexus_runtime.mlx_backend import Event, ModelSlot
from nexus_runtime.service_auth import ServiceTokenMiddleware
from fastapi import FastAPI

TOKEN = "test-service-token"


class FakeSettings:
    enable_thinking = False
    default_max_tokens = 256


class FakeBackend:
    def __init__(self) -> None:
        self.ready = True
        self.slots = {
            "chat": ModelSlot("chat", "mlx-community/Fake-8B-4bit", "always", obj=object()),
            "embed": ModelSlot("embed", "mlx-community/fake-embed", "always", obj=object()),
        }
        self.calls: list[dict] = []

    def resolve(self, kind, requested):
        slot = self.slots[kind]
        if not requested or requested.lower() in (slot.repo.lower(), slot.repo.rsplit("/", 1)[-1].lower()):
            return slot
        return None

    async def chat(self, messages, params, priority):
        self.calls.append({"messages": messages, "params": params, "priority": priority})
        for piece in ("Hello", ", world"):
            yield Event("text", piece)
        yield Event("done", finish_reason="stop", prompt_tokens=12, completion_tokens=3, cached_tokens=8)

    async def embed(self, texts, dimensions):
        return [[0.6, 0.8] for _ in texts], 7

    def status(self):
        return {"ready": self.ready, "models": [], "queue": {}}

    async def load(self, name):
        return {"name": name, "loaded": True}

    async def unload(self, name):
        return {"name": name, "loaded": False}


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
async def client(backend):
    app = FastAPI()
    app.add_middleware(ServiceTokenMiddleware)
    app.include_router(router)
    app.state.backend = backend
    app.state.settings = FakeSettings()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as c:
        yield c


# ── auth ─────────────────────────────────────────────────────────────────────


async def test_requests_without_the_token_are_refused(backend):
    app = FastAPI()
    app.add_middleware(ServiceTokenMiddleware)
    app.include_router(router)
    app.state.backend = backend
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/models")).status_code == 401
        assert (await c.get("/v1/models", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/v1/models", headers={"X-Service-Token": TOKEN})).status_code == 200
        # Liveness stays open for start.sh.
        assert (await c.get("/health")).status_code == 200


# ── chat ─────────────────────────────────────────────────────────────────────


async def test_a_completion_has_the_shape_the_openai_sdk_parses(client, backend):
    response = await client.post("/v1/chat/completions", json={
        "model": "mlx-community/Fake-8B-4bit",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello, world"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 15
    assert body["usage"]["prompt_tokens_details"]["cached_tokens"] == 8
    # No header means interactive.
    assert backend.calls[0]["priority"] is Priority.INTERACTIVE


async def test_the_short_model_name_is_accepted_and_others_are_not(client):
    ok = await client.post("/v1/chat/completions", json={
        "model": "Fake-8B-4bit", "messages": [{"role": "user", "content": "hi"}]})
    missing = await client.post("/v1/chat/completions", json={
        "model": "gpt-oss:20b", "messages": [{"role": "user", "content": "hi"}]})
    assert ok.status_code == 200
    assert missing.status_code == 404
    assert "Fake-8B-4bit" in missing.json()["detail"]


async def test_streaming_sends_deltas_a_finish_and_done(client):
    async with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True,
        "stream_options": {"include_usage": True},
    }) as response:
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(line[6:]) for line in lines[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "Hello, world"
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"]][-1] == "stop"
    assert chunks[-1]["usage"]["completion_tokens"] == 3


async def test_priority_header_and_parameters_reach_the_backend(client, backend):
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    await client.post(
        "/v1/chat/completions",
        headers={"X-Nexus-Priority": "bulk"},
        json={
            "messages": [{"role": "system", "content": [{"type": "text", "text": "be terse"}]},
                         {"role": "user", "content": "x?"}],
            "max_tokens": 64, "temperature": 0.0, "stop": "END",
            "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}},
            "reasoning_effort": "high",
        },
    )
    call = backend.calls[-1]
    assert call["priority"] is Priority.BULK
    assert call["messages"][0] == {"role": "system", "content": "be terse"}
    params = call["params"]
    assert (params.max_tokens, params.temperature, params.stop) == (64, 0.0, ["END"])
    assert params.json_schema == schema
    assert params.enable_thinking is True


async def test_non_text_content_is_refused(client):
    response = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}],
    })
    assert response.status_code == 400


async def test_a_loading_runtime_answers_503(client, backend):
    backend.ready = False
    response = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503


# ── embeddings, models, residency ────────────────────────────────────────────


async def test_embeddings_have_the_openai_shape(client):
    response = await client.post("/v1/embeddings", json={
        "model": "mlx-community/fake-embed", "input": ["a", "b"]})
    body = response.json()
    assert [d["index"] for d in body["data"]] == [0, 1]
    assert body["data"][0]["embedding"] == [0.6, 0.8]
    assert body["usage"]["prompt_tokens"] == 7


async def test_a_single_string_input_is_one_embedding(client):
    body = (await client.post("/v1/embeddings", json={"input": "solo"})).json()
    assert len(body["data"]) == 1


async def test_model_list_and_residency_endpoints(client):
    ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert ids == ["mlx-community/Fake-8B-4bit", "mlx-community/fake-embed"]
    assert (await client.post("/models/chat/unload")).json()["loaded"] is False
    assert (await client.post("/models/nope/load")).status_code == 404


async def test_an_unloaded_model_answers_503_before_any_streaming(client, backend):
    backend.slots["chat"].obj = None
    response = await client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert response.status_code == 503
    backend.slots["embed"].obj = None
    assert (await client.post("/v1/embeddings", json={"input": "x"})).status_code == 503
