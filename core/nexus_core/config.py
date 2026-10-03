"""Configuration for the core service.

Follows the ``_require`` pattern from ``memory/app/config.py`` so both services
fail fast and identically on a missing variable. The one deliberate difference:
model names are configuration, never literals in code. The memory service
hardcodes its model in ``main.py`` and that is the thing making a later swap to
local inference expensive.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env explicitly, rather than bare load_dotenv().
#
# Bare load_dotenv() searches UPWARD from this file's directory and stops at
# the first .env it finds — so a stray core/.env shadows the real one at the
# repo root, silently. That is not hypothetical: a core/.env holding an empty
# OPENAI_API_KEY made the service fail on startup with "not set" while the
# root .env sitting two directories up had the key all along (2026-09-02).
# An explicit path can only ever load the file we mean.
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ROOT_ENV)


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

        # ── Database (Supabase Postgres) ─────────────────────────────────────
        # Postgres stores what was said; Neo4j stores what was learned.
        self.database_url = _require("DATABASE_URL")
        self.database_echo = _optional("DATABASE_ECHO", "0") == "1"

        # Kept only as the fixed identity seeded by supabase/seed.sql, used in
        # tests. Real requests no longer use this — see auth.py; every route
        # now resolves the user from the session cookie, not from settings.
        self.demo_user_id = uuid.UUID(
            _optional("DEMO_USER_ID", "00000000-0000-0000-0000-000000000001")
        )

        # Only needed once Supabase Auth lands. The service role key bypasses
        # row level security entirely: it belongs to this process and must
        # never reach the browser via a NEXT_PUBLIC_* variable.
        self.supabase_url = os.getenv("SUPABASE_URL") or None
        self.supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None

        # ── Auth (added 2026-09-02) ─────────────────────────────────────────
        # Signs and verifies session JWTs. Required, not defaulted — a
        # guessable or empty secret means anyone can forge a login for any
        # user, so this fails loud rather than silently running insecure.
        self.jwt_secret = _require("JWT_SECRET")
        self.jwt_algorithm = "HS256"
        self.session_max_age_seconds = int(_optional("SESSION_MAX_AGE_SECONDS", str(60 * 60 * 24 * 30)))

        # Google OAuth is optional — the app runs fine on email/password alone
        # if these are left blank. google_oauth_enabled is what the route
        # checks before attempting anything, rather than failing at startup.
        self.google_client_id = os.getenv("GOOGLE_CLIENT_ID") or None
        self.google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET") or None
        self.google_oauth_enabled = bool(self.google_client_id and self.google_client_secret)

        # Where the browser gets sent back to after a Google login redirect.
        self.frontend_url = _optional("FRONTEND_URL", "http://localhost:3000")

        # False for local http development; a real deployment over https
        # must set this to "1", or the browser will refuse to store the
        # session cookie at all (browsers won't set Secure cookies over
        # plain http, and won't send them back either).
        self.session_cookie_secure = _optional("SESSION_COOKIE_SECURE", "0") == "1"

        # ── Service wiring ───────────────────────────────────────────────────
        self.memory_service_url = _optional("MEMORY_SERVICE_URL", "http://localhost:8000")
        self.rag_service_url = _optional("RAG_SERVICE_URL", "http://localhost:8001")
        self.searxng_url = _optional("SEARXNG_URL", "http://localhost:8080")
        self.port = int(_optional("CORE_PORT", "8100"))
        self.cors_origins = [
            origin.strip()
            for origin in _optional("CORE_CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        ]
