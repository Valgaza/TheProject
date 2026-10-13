"""Download everything voice, images and the decision model need, ahead of time.

The runtime never downloads an optional model at startup or on first use (a
slow connection would hold the whole stack hostage), so voice, vision and
Laya are fetched here once.
(Voice activity detection and Smart Turn come bundled with pipecat-ai.)

  speech-to-text      RUNTIME_STT_MODEL  (Parakeet, ~2.5 GB)
  text-to-speech      RUNTIME_TTS_MODEL  (Kokoro, ~350 MB)
  vision              CORE_VISION_MODEL  (Qwen3.5-4B, ~3 GB)
  decision model      RUNTIME_DECIDE_MODEL / RUNTIME_DECIDE_SUBFOLDER (~1.7 GB),
                      unless it is a local fine-tune

    uv run python scripts/fetch_voice_assets.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    env = {**dotenv_values(ROOT / ".env"), **os.environ}

    for label, repo in (("STT", env.get("RUNTIME_STT_MODEL") or "mlx-community/parakeet-tdt-0.6b-v2"),
                        ("TTS", env.get("RUNTIME_TTS_MODEL") or "mlx-community/Kokoro-82M-bf16"),
                        ("vision", env.get("RUNTIME_VISION_MODEL") or env.get("CORE_VISION_MODEL")
                         or "mlx-community/Qwen3.5-4B-MLX-4bit")):
        print(f"{label}: {repo}", flush=True)
        snapshot_download(repo)

    decide = env.get("RUNTIME_DECIDE_MODEL") or "convaiinnovations/laya"
    if (ROOT / decide).is_dir() or os.path.isdir(decide):
        print(f"decision model: local checkpoint {decide}")
    else:
        sub = (env.get("RUNTIME_DECIDE_SUBFOLDER", "typed-decisions") or "").strip()
        print(f"decision model: {decide} {sub}", flush=True)
        snapshot_download(decide, allow_patterns=[f"{sub}/*"] if sub else None)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
