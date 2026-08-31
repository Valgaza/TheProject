import os

from dotenv import load_dotenv

# Reads a local .env file (if one exists, see orchestrator/.env.example) and
# copies its key=value pairs into the process's environment variables, so
# os.getenv() below can see them. If there's no .env file, this just does
# nothing and every os.getenv() call below falls back to its default.
load_dotenv()


def _require(key: str) -> str:
    # Same pattern as memory/app/config.py: if the env var is missing, crash
    # immediately with a clear message, instead of silently falling back to
    # a guessed default (like "localhost") that might be wrong and would
    # only surface as a confusing connection failure later, mid-request.
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return value


class Settings:
    def __init__(self) -> None:
        # These three MUST be set — there's no safe default for "where is
        # the RAG/memory/SearXNG service," since guessing wrong wouldn't
        # fail loudly, it would just silently point at the wrong place.
        self.rag_url = _require("RAG_URL")
        self.memory_url = _require("MEMORY_URL")
        self.searxng_url = _require("SEARXNG_URL")

        # These, by contrast, are tuning knobs, not "is the system wired
        # correctly" checks — a missing value just means "use the sensible
        # default," not "something is broken." So os.getenv(key, default)
        # (fall back quietly) is the right pattern here, unlike above.
        self.rag_timeout = float(os.getenv("RAG_TIMEOUT_SECONDS", "30"))
        self.graph_timeout = float(os.getenv("GRAPH_TIMEOUT_SECONDS", "30"))
        self.web_timeout = float(os.getenv("WEB_TIMEOUT_SECONDS", "15"))
        self.cors_origins = [
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        ]


settings = Settings()
