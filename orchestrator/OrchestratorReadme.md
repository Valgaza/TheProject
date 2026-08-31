# Orchestrator

Routes a chat message across the RAG pipeline, knowledge graph, and (optionally) web search, assembles the results into context, and calls the LLM.

## Run it

```bash
cp orchestrator/.env.example orchestrator/.env
docker compose -f orchestrator/docker-compose.yml up -d   # starts SearXNG
source .venv/bin/activate
pip install -e "orchestrator"
uvicorn app.main:app --reload --port 8002 --app-dir orchestrator
```

## Status

- `POST /chat` — takes `{message, user_id, project_id?, use_web?}`, returns `{answer, used_rag, used_graph, used_web}`.
- Routing (`app/router.py`) is rule-based only right now: web search triggers on the `use_web` flag or phrases like "search the internet"; RAG and the graph are always both queried. No LLM-decided fallback yet — see `PROJECT_STATUS_REPORT.md`.
- `app/clients/llm_client.py` is a stub — it echoes the assembled prompt back instead of calling a real model. Swap `llm_client` for a real implementation of the `LLMClient` interface once the LLM service's API is decided.
- If a downstream call (RAG, graph, or web) fails, `main.py` currently swallows the error silently and just omits that context — no retry, no error surfaced to the caller. Worth deciding on real failure handling before this goes further.
