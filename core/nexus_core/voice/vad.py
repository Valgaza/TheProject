"""Is someone speaking? Per 32 ms of audio.

Silero VAD (v5), run with onnxruntime on the CPU: a 2 MB model that answers in
well under a millisecond per frame, which is why it runs here, in the voice
session, rather than a network hop away in the runtime. The model file is
fetched once to ~/.cache/nexus/silero_vad.onnx (scripts/fetch_voice_assets.py).

Without the file, a loudness threshold stands in. It cannot tell speech from a
door slamming, but it keeps voice usable and is what the tests use.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 512  # samples per decision: 32 ms at 16 kHz, what Silero v5 expects
MODEL_PATH = Path(os.getenv("NEXUS_VAD_MODEL", Path.home() / ".cache" / "nexus" / "silero_vad.onnx"))


class EnergyVAD:
    """Fallback: loud enough counts as speech."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def reset(self) -> None:
        pass

    def probability(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0
        return 1.0 if rms >= self.threshold else 0.0


class SileroVAD:
    CONTEXT = 64  # samples of the previous frame the model sees at 16 kHz

    def __init__(self, path: Path = MODEL_PATH) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.reshape(1, -1).astype(np.float32)], axis=1)
        out, self.state = self.session.run(
            None, {"input": x, "state": self.state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)}
        )
        self.context = x[:, -self.CONTEXT:]
        return float(out[0][0])


def make_vad():
    """Silero if its model is on disk, otherwise the loudness fallback."""
    if MODEL_PATH.exists():
        try:
            return SileroVAD(MODEL_PATH)
        except Exception:
            logger.exception("Silero VAD failed to load; using the loudness fallback")
    else:
        logger.warning("no VAD model at %s; using the loudness fallback "
                       "(run scripts/fetch_voice_assets.py)", MODEL_PATH)
    return EnergyVAD()
