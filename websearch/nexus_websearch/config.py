"""Configuration for the web search service.

It runs in a container beside SearXNG on the compose network, so its defaults
name those hosts: SearXNG as ``searxng``, the MLX runtime on the Mac as
``host.docker.internal``.
"""

from __future__ import annotations

import os


def _optional(key: str, default: str) -> str:
    return os.getenv(key) or default


class Settings:
    def __init__(self) -> None:
        self.searxng_url = _optional("SEARXNG_URL", "http://searxng:8080").rstrip("/")
        # The rewrite that strips identifying details is a model call, made
        # to the runtime like every other one.
        self.runtime_url = _optional("RUNTIME_URL", "http://host.docker.internal:8003").rstrip("/")
        self.rewrite_model = os.getenv("CORE_GATE_MODEL") or os.getenv("CORE_CHAT_MODEL") or ""
        self.service_token = os.getenv("SERVICE_TOKEN", "")
        # Append-only record of every query that left the machine.
        self.audit_log = _optional("WEBSEARCH_AUDIT_LOG", "/data/audit.jsonl")
        self.cache_seconds = int(_optional("WEBSEARCH_CACHE_SECONDS", "600"))
        self.rate_per_minute = int(_optional("WEBSEARCH_RATE_PER_MINUTE", "12"))
        self.max_results = int(_optional("WEBSEARCH_MAX_RESULTS", "5"))
        # The decision model's last check on an outgoing query (Laya, on the
        # runtime): off | shadow | live. Live blocks at or above the bar.
        self.laya_query = _optional("WEBSEARCH_LAYA_QUERY", "shadow")
        self.laya_block_above = float(_optional("WEBSEARCH_LAYA_BLOCK_ABOVE", "0.8"))
