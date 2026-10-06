"""The runtime's speech models, as the voice session uses them."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class RuntimeVoice:
    def __init__(self, http: httpx.AsyncClient, root: str) -> None:
        self._http = http  # carries the service token
        self._root = root.rstrip("/")

    async def preload(self) -> None:
        """Load speech-to-text and text-to-speech now, so the first thing
        someone says is not the slow one. Errors surface on first use."""
        for name in ("stt", "tts"):
            try:
                await self._http.post(f"{self._root}/models/{name}/load", timeout=300)
            except Exception as exc:
                logger.warning("could not preload %s: %s", name, exc)

    async def transcribe(self, pcm16: bytes) -> str:
        r = await self._http.post(
            f"{self._root}/v1/audio/transcriptions", content=pcm16,
            headers={"Content-Type": "application/octet-stream"}, timeout=60,
        )
        r.raise_for_status()
        return r.json().get("text", "")

    async def speak(self, text: str) -> bytes:
        r = await self._http.post(f"{self._root}/v1/audio/speech", json={"input": text}, timeout=60)
        r.raise_for_status()
        return r.content
