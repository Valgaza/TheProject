"""Configuration for the core service.

Follows the ``_require`` pattern from ``memory/app/config.py`` so both services
fail fast and identically on a missing variable. The one deliberate difference:
model names are configuration, never literals in code. The memory service
hardcodes its model in ``main.py`` and that is the thing making a later swap to
local inference expensive.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return value


def _optional(key: str, default: str) -> str:
    value = os.getenv(key)
    return value if value else default


class Settings:
    """Resolved once at startup. Construct inside a lifespan, not at import."""

    def __init__(self) -> None:
        # ── Providers ────────────────────────────────────────────────────────
        self.openai_api_key = _require("OPENAI_API_KEY")
        self.openai_base_url = os.getenv("OPENAI_BASE_URL") or None

        # Model names are env-driven so that pointing the whole system at a
        # local runtime later is a config change, not a refactor. OPENAI_BASE_URL
        # is the other half of that: an OpenAI-compatible local server (Ollama,
        # vLLM, LM Studio) needs only these two to take over.
        self.chat_model = _optional("CORE_CHAT_MODEL", "gpt-5.4-nano-2026-03-17")
        self.embed_model = _optional("CORE_EMBED_MODEL", "text-embedding-3-small")
        # The write gate (phase 5) runs as its own cheap call so it can later be
        # served by a small local model while answering stays on a bigger one.
        self.gate_model = _optional("CORE_GATE_MODEL", self.chat_model)

        # ── Service wiring ───────────────────────────────────────────────────
        self.memory_service_url = _optional("MEMORY_SERVICE_URL", "http://localhost:8000")
        self.port = int(_optional("CORE_PORT", "8100"))
        self.cors_origins = [
            origin.strip()
            for origin in _optional("CORE_CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        ]
