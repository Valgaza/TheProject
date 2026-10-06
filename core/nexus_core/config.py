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

        # ── Database (Postgres) ──────────────────────────────────────────────
        # Postgres stores what was said; Neo4j stores what was learned.
        self.database_url = _require("DATABASE_URL")
        self.database_echo = _optional("DATABASE_ECHO", "0") == "1"

        # Kept only as the fixed identity seeded by db/seed.sql, used in
        # tests. Real requests no longer use this — see auth.py; every route
        # now resolves the user from the session cookie, not from settings.
        self.demo_user_id = uuid.UUID(
            _optional("DEMO_USER_ID", "00000000-0000-0000-0000-000000000001")
        )

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
        # How relevant a document chunk has to be before it is put in front
        # of the model. The reranker applies a sigmoid, so this is a 0-1
        # probability from a cross-encoder, not a cosine -- do not copy
        # memory's 0.45 across, the two numbers measure different things.
        #
        # This is the floor that lets documents be a retrieval tier rather
        # than a route: every turn retrieves, and only turns with a genuine
        # match pay for the context. Provisional at 0.5 -- "more likely
        # relevant than not" -- and wants the same treatment memory's floor
        # got: a sweep over real questions, not a round number. Raise it if
        # the corpus starts answering questions it has nothing to say about.
        self.rag_min_score = float(_optional("CORE_RAG_MIN_SCORE", "0.5"))

        self.memory_service_url = _optional("MEMORY_SERVICE_URL", "http://localhost:8000")
        self.rag_service_url = _optional("RAG_SERVICE_URL", "http://localhost:8001")
        # Shared secret sent to memory and RAG on every call; they refuse
        # requests without it. Any long random string, the same in all three.
        self.service_token = _require("SERVICE_TOKEN")
        # The websearch service (websearch/, a container beside SearXNG): the
        # only way anything reaches the internet. It redacts and audits.
        self.websearch_url = _optional("WEBSEARCH_URL", "http://localhost:8004")

        # ── Decision model (Laya, served by the runtime) ────────────────────
        # Per decision point: off | shadow | live (see decisions.py). Shadow by
        # default: recorded beside the existing logic, never acted on, until
        # scripts/laya/evaluate.py says a point has earned live.
        self.laya_gate = _optional("CORE_LAYA_GATE", "shadow")
        self.laya_web_intent = _optional("CORE_LAYA_WEB_INTENT", "shadow")
        # Live only: below this probability of "worth remembering", the write
        # gate skips its model call and records a decline.
        self.laya_gate_skip_below = float(_optional("CORE_LAYA_GATE_SKIP_BELOW", "0.15"))
        # Live only: at or above this probability a message is treated as
        # asking for the web. High on purpose -- a false yes sends words off
        # the machine.
        self.laya_web_intent_above = float(_optional("CORE_LAYA_WEB_INTENT_ABOVE", "0.9"))

        # ── Voice (voice/session.py) ────────────────────────────────────────
        # A pause this long may end the person's turn; this long always does.
        self.voice_eot_silence_ms = int(_optional("CORE_VOICE_EOT_SILENCE_MS", "500"))
        self.voice_max_silence_ms = int(_optional("CORE_VOICE_MAX_SILENCE_MS", "1200"))
        # Transcription and retrieval start at this much silence, so they are
        # done when the turn ends; thrown away if speech resumes. 0 = off.
        self.voice_speculate_silence_ms = int(_optional("CORE_VOICE_SPECULATE_SILENCE_MS", "250"))
        # Speech over the assistant for this long pauses it to check whether
        # it is an interruption.
        self.voice_barge_in_ms = int(_optional("CORE_VOICE_BARGE_IN_MS", "300"))
        # Speech-probability thresholds; the second applies while the
        # assistant is talking, so its own voice leaking back is not speech.
        self.voice_speech_threshold = float(_optional("CORE_VOICE_SPEECH_THRESHOLD", "0.5"))
        self.voice_barge_threshold = float(_optional("CORE_VOICE_BARGE_THRESHOLD", "0.8"))
        self.laya_end_of_turn = _optional("CORE_LAYA_END_OF_TURN", "shadow")
        self.laya_barge_in = _optional("CORE_LAYA_BARGE_IN", "shadow")
        self.port = int(_optional("CORE_PORT", "8100"))
        self.cors_origins = [
            origin.strip()
            for origin in _optional("CORE_CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        ]
