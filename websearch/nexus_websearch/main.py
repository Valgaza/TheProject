"""The web search service: the one component allowed to reach the internet.

    POST /search   {"message", "known_names", "user_id"}  -> what was sent, and results
    GET  /audit    the most recent entries of the audit log
    GET  /health   liveness (open)

Runs in a container on the compose network beside SearXNG, whose port is not
published to the host: nothing on the Mac can query SearXNG except through
here, and everything that comes through here is redacted first and written
to the audit log -- the exact text that left, never the message it came from.

A search never raises to the caller. Every failure is an outcome: "the
rewrite failed, nothing was sent", "blocked: still identifies someone",
"SearXNG is unreachable". Core turns each into a sentence the model can say.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import Settings
from .redaction import REWRITE_PROMPT, strip_identifiers

logger = logging.getLogger("nexus_websearch")

# Tried in order when a search comes back empty: SearXNG's default mix first,
# then explicit engines, so one engine blocking us is not "no results".
ENGINE_FALLBACKS: list[str | None] = [None, "duckduckgo,brave", "wikipedia"]

# The outgoing query check, phrased like core's other decision questions.
QUERY_QUESTIONS = {
    "identifies_someone": {
        "type": "choice",
        "instructions": "Does `query` point at a specific individual or private matter?",
        "criteria": {
            "identifies": "it contains a person's name, an ID or case number, an email, or "
                          "another detail identifying a particular person, client or patient",
            "generic": "it is only about a general topic anyone could search for",
        },
    },
}


class SearchRequest(BaseModel):
    message: str
    known_names: list[str] = Field(default_factory=list)
    user_id: str | None = None


class Result(BaseModel):
    title: str
    url: str
    snippet: str


class SearchResponse(BaseModel):
    query_sent: str | None
    results: list[Result] = []
    error: str | None = None
    stripped: list[str] = []
    blocked: bool = False


class _Auth:
    """Only core may ask for a search. Same check as the other services."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        import os

        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        expected = os.environ.get("SERVICE_TOKEN", "").encode()
        sent = dict(scope["headers"]).get(b"x-service-token", b"")
        if expected and hmac.compare_digest(sent, expected):
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"missing or invalid service token"}'})


class Searcher:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = http
        self.cache: dict[str, tuple[float, list[dict]]] = {}
        self.recent: dict[str, deque] = {}

    # ── the pieces ───────────────────────────────────────────────────────────

    def _rate_ok(self, user: str) -> bool:
        window = self.recent.setdefault(user, deque())
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.s.rate_per_minute:
            return False
        window.append(now)
        return True

    async def _rewrite(self, message: str) -> str | None:
        try:
            r = await self.http.post(
                f"{self.s.runtime_url}/v1/chat/completions",
                json={
                    "model": self.s.rewrite_model,
                    "messages": [{"role": "system", "content": REWRITE_PROMPT},
                                 {"role": "user", "content": message}],
                    "temperature": 0.0,
                    "max_tokens": 64,
                },
                headers={"Authorization": f"Bearer {self.s.service_token}",
                         "X-Nexus-Priority": "interactive"},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip().strip('"')
        except Exception as exc:
            logger.warning("rewrite failed; nothing will be sent: %s", exc)
            return None

    async def _check(self, query: str) -> float | None:
        """Probability the query still identifies someone, or None."""
        if self.s.laya_query not in ("shadow", "live"):
            return None
        try:
            r = await self.http.post(
                f"{self.s.runtime_url}/decide",
                json={"state": {"query": query}, "questions": QUERY_QUESTIONS},
                headers={"X-Service-Token": self.s.service_token, "X-Nexus-Priority": "live"},
                timeout=3,
            )
            r.raise_for_status()
            return float(r.json()["answers"]["identifies_someone"]["probs"]["identifies"])
        except Exception as exc:
            logger.info("query check unavailable: %s", exc)
            return None

    async def _searxng(self, query: str) -> list[dict]:
        cached = self.cache.get(query)
        if cached and time.monotonic() - cached[0] < self.s.cache_seconds:
            return cached[1]
        last_error: Exception | None = None
        for engines in ENGINE_FALLBACKS:
            params = {"q": query, "format": "json"}
            if engines:
                params["engines"] = engines
            try:
                r = await self.http.get(f"{self.s.searxng_url}/search", params=params, timeout=15)
                r.raise_for_status()
                raw = r.json().get("results", [])
            except Exception as exc:
                last_error = exc
                continue
            if raw:
                results = [{"title": x.get("title", ""), "url": x.get("url", ""),
                            "snippet": x.get("content", "")} for x in raw[: self.s.max_results]]
                self.cache[query] = (time.monotonic(), results)
                return results
        if last_error is not None:
            raise last_error
        return []

    def _audit(self, entry: dict) -> None:
        path = Path(self.s.audit_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    # ── a search ─────────────────────────────────────────────────────────────

    async def search(self, req: SearchRequest) -> SearchResponse:
        if not self._rate_ok(req.user_id or "anonymous"):
            return SearchResponse(query_sent=None, error="too many web searches in the last minute")

        rewritten = await self._rewrite(req.message)
        if not rewritten:
            return SearchResponse(query_sent=None, error="could not prepare a safe query")
        query, removed = strip_identifiers(rewritten, req.known_names)
        if not query:
            return SearchResponse(query_sent=None, error="nothing left to search after redaction",
                                  stripped=removed)

        identifies = await self._check(query)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user_id": req.user_id,
                 "query_sent": query, "stripped": removed, "identifies_p": identifies,
                 "check_mode": self.s.laya_query}
        if self.s.laya_query == "live" and identifies is not None and identifies >= self.s.laya_block_above:
            # Nothing leaves. Recorded with the text that *would* have.
            entry.update(query_sent=None, blocked_query=query, blocked=True)
            self._audit(entry)
            return SearchResponse(query_sent=None, blocked=True, stripped=removed,
                                  error="blocked: the query may still identify someone")

        try:
            results = await self._searxng(query)
            entry["results"] = len(results)
            self._audit(entry)
            return SearchResponse(query_sent=query, results=[Result(**r) for r in results], stripped=removed)
        except Exception as exc:
            entry["error"] = exc.__class__.__name__
            self._audit(entry)
            return SearchResponse(query_sent=query, stripped=removed,
                                  error=f"search failed: {exc.__class__.__name__}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    settings = Settings()
    http = httpx.AsyncClient()
    app.state.searcher = Searcher(settings, http)
    app.state.settings = settings
    yield
    await http.aclose()


app = FastAPI(title="Nexus Web Search", version="0.1.0", lifespan=lifespan)
app.add_middleware(_Auth)


@app.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    if not body.message.strip():
        raise HTTPException(400, "message is empty")
    return await request.app.state.searcher.search(body)


@app.get("/audit")
async def audit(request: Request, limit: int = 50):
    """The most recent audit entries, newest last."""
    path = Path(request.app.state.settings.audit_log)
    if not path.exists():
        return {"entries": []}
    lines = path.read_text().splitlines()[-max(1, min(limit, 1000)):]
    return {"entries": [json.loads(line) for line in lines]}


@app.get("/health")
async def health(request: Request):
    """Liveness, and whether SearXNG answers."""
    s = request.app.state.settings
    try:
        r = await request.app.state.searcher.http.get(s.searxng_url, timeout=3)
        searxng = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        searxng = "unreachable"
    return {"status": "ok", "searxng": searxng}
