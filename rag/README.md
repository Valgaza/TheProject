# rag

Document retrieval for Nexus: the `rag_pipeline` package and the HTTP service
that exposes it (`rag_api.py`, port 8001). Qdrant hybrid search (dense +
BM25), a cross-encoder reranker, and context assembly. Embeddings and
reranking run in-process on CPU.

Only `core` calls this service. Every request needs core's `SERVICE_TOKEN`,
and every read is scoped to the tiers the asking user may see
(`rag_pipeline/scopes.py`). Why it is a separate service at all:
[`docs/RAG_API_SERVICE.md`](../docs/RAG_API_SERVICE.md).

## Running it

It is a member of the repo's uv workspace, so the root setup installs it.
`start.sh` runs it alongside everything else; by hand, from the repo root:

```bash
uv run uvicorn rag_api:app --app-dir rag --reload --port 8001
```

Settings live in the root `.env` (see the RAG pipeline section of
`.env.example`); defaults are in `rag_pipeline/config.py`.

## The CLI

For poking at the pipeline directly, outside any user's scope:

```bash
uv run rag ingest rag/sample_docs/sample.md
uv run rag query "What is hybrid search?"
uv run rag status
```

## Tests

`uv run pytest rag/tests`, also part of the default root run. The
isolation guarantees are `tests/test_scopes.py` (the rule) and
`tests/test_scope_isolation.py` (the rule, enforced by a real pipeline).

```
Documents → Loaders → Chunking → Embeddings → Qdrant (dense + sparse)
                                                    ↓
User query → Embed → Hybrid search → Rerank → Context assembly → core
```
