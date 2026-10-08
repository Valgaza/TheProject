"""The runtime's speech models, as Pipecat services.

Speech-to-text and text-to-speech stay in the MLX runtime, under its GPU
scheduler at live priority; these only adapt Pipecat's service interfaces to
the runtime's two endpoints (voice/runtime.py). Pipecat's own OpenAI services
don't fit: the runtime takes and returns raw PCM16, not uploaded files and
encoded audio.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.utils.time import time_now_iso8601

from .runtime import RuntimeVoice

TTS_SAMPLE_RATE = 24_000  # Kokoro's


class RuntimeSTTService(SegmentedSTTService):
    """Parakeet in the runtime, once per stretch of speech the VAD marks out.

    Segmented rather than streaming: the runtime transcribes a whole utterance
    in well under 200 ms, so a segment's transcript lands about as soon as a
    streaming one would have settled."""

    def __init__(self, *, voice: RuntimeVoice, **kwargs) -> None:
        super().__init__(settings=STTSettings(model=None, language=None), **kwargs)
        self._voice = voice

    @property
    def wants_wav_segments(self) -> bool:
        return False  # the runtime reads raw PCM16 at 16 kHz

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        await self.start_processing_metrics()
        try:
            text = (await self._voice.transcribe(audio)).strip()
        except Exception as exc:
            yield ErrorFrame(error=f"transcription failed: {exc}")
            return
        finally:
            await self.stop_processing_metrics()
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())


class RuntimeTTSService(TTSService):
    """Kokoro in the runtime, one piece at a time.

    The Nexus turn step cuts the answer into pieces to say (a long opening
    sentence from its first clause, so the wait is short) and each is
    synthesised as it arrives, the next while this one plays. ``on_piece``
    hears of each piece and how long it lasts, in the order they will play."""

    def __init__(self, *, voice: RuntimeVoice, on_piece: Callable[[str, float], None] | None = None,
                 **kwargs) -> None:
        kwargs.setdefault("text_aggregation_mode", TextAggregationMode.TOKEN)
        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model=None, voice=None, language=None), **kwargs)
        self._voice = voice
        self._on_piece = on_piece

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        text = text.strip()
        if not text:
            return
        try:
            pcm = await self._voice.speak(text)
        except Exception as exc:
            yield ErrorFrame(error=f"speech synthesis failed: {exc}")
            return

        if self._on_piece is not None:
            self._on_piece(text, len(pcm) / 2 / TTS_SAMPLE_RATE)

        async def chunks():
            yield pcm

        await self.start_tts_usage_metrics(text)
        async for frame in self._stream_audio_frames_from_iterator(
            chunks(), in_sample_rate=TTS_SAMPLE_RATE, context_id=context_id
        ):
            await self.stop_ttfb_metrics()
            yield frame
