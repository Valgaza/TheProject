"""Configuration for the runtime service.

Same ``_require``/``_optional`` shape as core and memory. Model names are
Hugging Face repo ids of MLX conversions (``mlx-community/...``): the runtime
downloads them on first use and serves them under exactly that id, so core's
``CORE_CHAT_MODEL`` and memory's ``MEMORY_EXTRACTION_MODEL`` name the same
string the runtime loaded.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# The repo-root .env, explicitly, for the reason RUNBOOK gives: a bare
# load_dotenv() stops at the first .env it finds walking upward.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _optional(key: str, default: str) -> str:
    return os.getenv(key) or default


class Settings:
    def __init__(self) -> None:
        self.port = int(_optional("RUNTIME_PORT", "8003"))

        # The one resident chat model, serving every caller: answers, the
        # write gate, titles, the web rewrite, graph extraction. Defaults to
        # what core and memory ask for, so the name is set in one place.
        self.chat_model = _optional(
            "RUNTIME_CHAT_MODEL", _optional("CORE_CHAT_MODEL", "mlx-community/Qwen3-8B-4bit")
        )
        # 768-d, the dimension memory's graph is configured for.
        self.embed_model = _optional(
            "RUNTIME_EMBED_MODEL",
            _optional("MEMORY_EMBEDDING_MODEL", "mlx-community/embeddinggemma-300m-4bit"),
        )

        # Hybrid reasoning models (Qwen3 and later) think before answering
        # unless told not to. Off by default: thinking tokens are latency the
        # person waits through and never sees. A request can still ask for it
        # with reasoning_effort.
        self.enable_thinking = _optional("RUNTIME_ENABLE_THINKING", "0") == "1"

        self.default_max_tokens = int(_optional("RUNTIME_DEFAULT_MAX_TOKENS", "4096"))

        # Reused KV for each conversation's shared prefix (system prompt plus
        # history), so a follow-up turn only processes what is new.
        self.prompt_cache_entries = int(_optional("RUNTIME_PROMPT_CACHE_ENTRIES", "8"))
        self.prompt_cache_gb = float(_optional("RUNTIME_PROMPT_CACHE_GB", "1"))

        # Ceiling on what MLX may allocate, in GB. Unset leaves MLX's default,
        # which follows macOS's GPU wired-memory limit.
        limit = os.getenv("RUNTIME_MEMORY_LIMIT_GB")
        self.memory_limit_gb = float(limit) if limit else None

        # MLX keeps freed buffers for reuse, unbounded by default: the first
        # MLX baseline peaked at 11 GB of footprint against 5.2 GB of weights.
        # Capped so what the runtime holds is what it is using.
        self.mlx_cache_limit_gb = float(_optional("RUNTIME_MLX_CACHE_LIMIT_GB", "0.5"))
        # Prompt tokens processed per step. Graphiti's extraction prompts run
        # to ~1,500 tokens; processing them in smaller steps trades a little
        # prefill speed for a much lower activation peak.
        self.prefill_step = int(_optional("RUNTIME_PREFILL_STEP", "512"))

        # Shared with core and memory. Accepted as X-Service-Token or as an
        # OpenAI-style "Authorization: Bearer", so OPENAI_API_KEY can simply
        # be this value.
        self.service_token = _require("SERVICE_TOKEN")
