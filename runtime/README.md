# runtime

Every local model Nexus uses, on Apple's MLX, in one process (port 8003),
behind an OpenAI-compatible API. Core and the memory service (through
graphiti) call it with the OpenAI SDK; the only configuration they need is
`OPENAI_BASE_URL=http://localhost:8003/v1` and the service token as their API
key. Operating it day to day: [`RUNBOOK.md`](../RUNBOOK.md), "Running the models
locally".

## What it serves

| Endpoint | For |
|---|---|
| `POST /v1/chat/completions` | answers, the write gate, titles, the web rewrite, graph extraction. Streaming, `stop`, `response_format` (`json_schema` / `json_object`), `reasoning_effort` |
| `POST /v1/embeddings` | graph embeddings (EmbeddingGemma, 768-d; `dimensions` truncates Matryoshka-style) |
| `GET /v1/models` | the models currently loaded |
| `GET /health` | `"loading"` until every always-resident model is loaded and warmed, then `"ok"`. Open, no token |
| `GET /models`, `POST /models/{chat\|embed}/{load\|unload}` | residency: what is in memory, how big, and moving it in or out |

## How it is put together

- `engine.py`: one worker thread owns the GPU and runs jobs a step at a time
  (a step is one generated token), picking the most urgent job before each
  step. `X-Nexus-Priority: live | interactive | bulk` sets urgency.
- `mlx_backend.py`: the models. Generation on mlx-lm with a per-conversation
  prompt cache; JSON-schema constrained decoding with xgrammar (compact JSON,
  properties in schema order); embeddings on mlx-embeddings; load, unload and
  warm-up.
- `api.py`: the OpenAI-shaped routes. `service_auth.py`: the token check,
  accepting `X-Service-Token` or `Authorization: Bearer`.

Models are named by Hugging Face id (`mlx-community/...`) in `.env`
(`CORE_CHAT_MODEL`, `MEMORY_EMBEDDING_MODEL`, overridable with
`RUNTIME_CHAT_MODEL` / `RUNTIME_EMBED_MODEL`) and downloaded on first start.

## Tests

```bash
uv run pytest runtime/tests                        # scheduler, filters, API shapes, auth: no models
RUN_MLX=1 uv run pytest runtime/tests -v           # real MLX on small models (Qwen3-0.6B, EmbeddingGemma)
```
