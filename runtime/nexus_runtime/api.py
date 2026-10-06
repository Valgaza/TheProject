"""The OpenAI-compatible surface: what core and graphiti actually call.

Kept to the parts of the API this system uses -- chat completions (streaming
or not, with ``response_format``), embeddings, and the model list -- plus the
runtime's own residency endpoints. Anything else gets a 404 rather than a
half-implementation that silently behaves differently from OpenAI's.

``X-Nexus-Priority: live | interactive | bulk`` on a chat request says how
urgent it is (engine.py). Missing means interactive.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from .engine import Priority
from .mlx_backend import GenParams

router = APIRouter()

PRIORITY_HEADER = "x-nexus-priority"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[dict[str, Any]]
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: str | None = None


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    input: str | list[str]
    dimensions: int | None = None
    encoding_format: str | None = None


def _backend(request: Request):
    backend = request.app.state.backend
    if not backend.ready:
        raise HTTPException(503, "runtime is still loading its models")
    return backend


def _text_of(content: Any) -> str:
    """OpenAI content is a string or a list of typed parts; the models here
    take text, so the text parts are joined and anything else is refused."""
    if content is None or isinstance(content, str):
        return content or ""
    parts = []
    for part in content:
        if part.get("type") != "text":
            raise HTTPException(400, f"unsupported content part: {part.get('type')}")
        parts.append(part.get("text", ""))
    return "".join(parts)


def _schema_of(response_format: dict[str, Any] | None) -> dict | None:
    if not response_format:
        return None
    kind = response_format.get("type")
    if kind == "json_schema":
        spec = response_format.get("json_schema") or {}
        schema = spec.get("schema")
        if not isinstance(schema, dict):
            raise HTTPException(400, "response_format.json_schema.schema is required")
        return schema
    if kind == "json_object":
        return {"type": "object"}
    if kind == "text":
        return None
    raise HTTPException(400, f"unsupported response_format type: {kind}")


def _params(body: ChatRequest, settings) -> GenParams:
    stop = [body.stop] if isinstance(body.stop, str) else list(body.stop or [])
    effort = (body.reasoning_effort or "").lower()
    thinking = settings.enable_thinking if not effort else effort in ("medium", "high")
    return GenParams(
        max_tokens=body.max_completion_tokens or body.max_tokens or settings.default_max_tokens,
        # 0.7 rather than OpenAI's 1.0: these are small local models, and at
        # 1.0 they wander. Callers that care pass their own.
        temperature=0.7 if body.temperature is None else body.temperature,
        top_p=1.0 if body.top_p is None else body.top_p,
        stop=stop,
        json_schema=_schema_of(body.response_format),
        enable_thinking=thinking,
    )


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatRequest, request: Request):
    backend = _backend(request)
    slot = backend.resolve("chat", body.model)
    if slot is None:
        raise HTTPException(404, f"model {body.model!r} is not served here; this runtime serves "
                                 f"{backend.slots['chat'].repo!r}")
    if not slot.loaded:
        # Checked before streaming starts: once a 200 is on the wire there is
        # no way left to say "unavailable".
        raise HTTPException(503, f"{slot.repo} is not loaded right now")
    messages = [{"role": m.get("role", "user"), "content": _text_of(m.get("content"))}
                for m in body.messages]
    params = _params(body, request.app.state.settings)
    priority = Priority.parse(request.headers.get(PRIORITY_HEADER))
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not body.stream:
        text, done = [], None
        async for event in backend.chat(messages, params, priority):
            if event.kind == "text":
                text.append(event.text)
            elif event.kind == "error":
                raise HTTPException(500, event.text)
            else:
                done = event
        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": slot.repo,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "".join(text)},
                "finish_reason": done.finish_reason,
            }],
            "usage": _usage(done),
        })

    include_usage = bool((body.stream_options or {}).get("include_usage"))

    async def events():
        def chunk(delta: dict, finish: str | None = None) -> str:
            return "data: " + json.dumps({
                "id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": slot.repo,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }) + "\n\n"

        yield chunk({"role": "assistant", "content": ""})
        async for event in backend.chat(messages, params, priority):
            if event.kind == "text":
                yield chunk({"content": event.text})
            elif event.kind == "error":
                yield "data: " + json.dumps({"error": {"message": event.text}}) + "\n\n"
                break
            else:
                yield chunk({}, event.finish_reason)
                if include_usage:
                    yield "data: " + json.dumps({
                        "id": completion_id, "object": "chat.completion.chunk", "created": created,
                        "model": slot.repo, "choices": [], "usage": _usage(event),
                    }) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _usage(done) -> dict:
    prompt = done.prompt_tokens if done else 0
    completion = done.completion_tokens if done else 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": done.cached_tokens if done else 0},
    }


@router.post("/v1/embeddings")
async def embeddings(body: EmbeddingRequest, request: Request):
    backend = _backend(request)
    slot = backend.resolve("embed", body.model)
    if slot is None:
        raise HTTPException(404, f"model {body.model!r} is not served here; this runtime serves "
                                 f"{backend.slots['embed'].repo!r}")
    if not slot.loaded:
        raise HTTPException(503, f"{slot.repo} is not loaded right now")
    texts = [body.input] if isinstance(body.input, str) else body.input
    if not texts:
        raise HTTPException(400, "input is empty")
    vectors, used = await backend.embed(texts, body.dimensions)
    return {
        "object": "list",
        "model": slot.repo,
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "usage": {"prompt_tokens": used, "total_tokens": used},
    }


class DecideRequest(BaseModel):
    state: Any
    questions: dict[str, dict[str, Any]]


@router.post("/decide")
async def decide(body: DecideRequest, request: Request):
    """Typed decisions in one forward pass (the Laya decision model).

    ``questions`` maps an id to ``{"type": "noul" | "choice" | "score",
    "instructions": ..., "criteria": ...}``. Answers come back per id: ``p``
    (probability of yes) for noul, ``label`` and ``probs`` for choice.
    """
    backend = _backend(request)
    slot = backend.slots.get("decide")
    if slot is None or not slot.loaded:
        raise HTTPException(503, "the decision model is not loaded")
    if not body.questions:
        raise HTTPException(400, "questions is empty")
    try:
        result = await backend.decide(body.state, body.questions)
    except ValueError as exc:
        # Laya validates question shapes and says precisely what is wrong.
        raise HTTPException(400, str(exc)) from exc
    return {"model": slot.repo, **result}


@router.get("/v1/models")
async def list_models(request: Request):
    backend = request.app.state.backend
    return {
        "object": "list",
        "data": [{"id": s.repo, "object": "model", "owned_by": "nexus-runtime"}
                 for s in backend.slots.values() if s.loaded],
    }


@router.get("/health")
async def health(request: Request):
    backend = request.app.state.backend
    return {"status": "ok" if backend.ready else "loading", **backend.status()}


@router.get("/models")
async def models(request: Request):
    return request.app.state.backend.status()


@router.post("/models/{name}/load")
async def load_model(name: str, request: Request):
    backend = request.app.state.backend
    if name not in backend.slots:
        raise HTTPException(404, f"no model slot {name!r}; slots are {sorted(backend.slots)}")
    return await backend.load(name)


@router.post("/models/{name}/unload")
async def unload_model(name: str, request: Request):
    backend = request.app.state.backend
    if name not in backend.slots:
        raise HTTPException(404, f"no model slot {name!r}; slots are {sorted(backend.slots)}")
    return await backend.unload(name)
