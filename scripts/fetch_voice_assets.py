"""Download everything voice and the decision model need, ahead of time.

The runtime never downloads an optional model at startup (a slow connection
would hold the whole stack hostage), so voice and Laya are fetched here once:

  Silero VAD          ~/.cache/nexus/silero_vad.onnx (2 MB, read by core)
  speech-to-text      RUNTIME_STT_MODEL  (Parakeet, ~2.5 GB)
  text-to-speech      RUNTIME_TTS_MODEL  (Kokoro, ~350 MB)
  decision model      RUNTIME_DECIDE_MODEL / RUNTIME_DECIDE_SUBFOLDER (~1.7 GB),
                      unless it is a local fine-tune

    uv run python scripts/fetch_voice_assets.py
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent
SILERO_URL = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
SILERO_PATH = Path(os.getenv("NEXUS_VAD_MODEL", Path.home() / ".cache" / "nexus" / "silero_vad.onnx"))


def main() -> int:
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    if SILERO_PATH.exists():
        print(f"VAD: already at {SILERO_PATH}")
    else:
        SILERO_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SILERO_URL, SILERO_PATH)
        print(f"VAD: saved to {SILERO_PATH}")

    for label, repo in (("STT", env.get("RUNTIME_STT_MODEL") or "mlx-community/parakeet-tdt-0.6b-v2"),
                        ("TTS", env.get("RUNTIME_TTS_MODEL") or "mlx-community/Kokoro-82M-bf16")):
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
