"""One hands-free conversation: listening, answering aloud, being interrupted.

The browser streams microphone audio in (16 kHz PCM16, as fast as it is
recorded) and plays back the audio this sends. Everything in between happens
here:

    listening ──speech──> hearing ──short silence──> transcribe
        ^                    ^                           │
        │                    └──speech resumes───────────┤ (Laya: unfinished?
        │                                                │  then wait longer)
        │                                                v
        └──played to the end── answering: the turn streams, each finished
                               sentence is synthesised and sent while the
                               model writes the next
                                     │
                         speech over the assistant
                                     v
                     pause playback, transcribe the overlap:
                     "mm-hmm" -> resume;  anything else -> stop, store what
                     was actually heard, and start hearing the new utterance

Silence and speech are measured in audio time (frames received), not wall
time, so a stalled network cannot fake a pause.

Two places use the decision model, both in shadow by default: whether a
transcript is a finished thought (end of turn) and whether speech over the
assistant is an interruption or a backchannel (barge-in). Shadow means the
answer is logged next to what the rules decided, and the rules act.

Nothing here touches FastAPI or the database: the turn itself (store, recall,
retrieve, stream, store) comes in as a TurnBackend, which routers/voice.py
implements on chat_service and the tests fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

import numpy as np

from ..decisions import BARGE_IN_QUESTIONS, END_OF_TURN_QUESTIONS, Decision
from .text import SentenceSplitter
from .vad import FRAME, SAMPLE_RATE

logger = logging.getLogger(__name__)

FRAME_MS = FRAME * 1000 // SAMPLE_RATE  # 32

# Said over the assistant to show attention, not to take the turn.
BACKCHANNEL = re.compile(
    r"(?:mm+[- ]?hmm+|uh[- ]?huh|mhm+|hmm+|yeah|yep|yes|okay|ok|right|sure|i see|got it|"
    r"alright|cool|nice|wow|oh|ah|uh|um)(?:[ ,]+(?:yeah|yes|okay|ok|right|sure|got it))*",
    re.IGNORECASE,
)


def is_backchannel(text: str) -> bool:
    words = re.sub(r"[^\w\s'-]", " ", text).strip()
    return not words or bool(BACKCHANNEL.fullmatch(" ".join(words.split())))


@dataclass
class VoiceConfig:
    speech_threshold: float = 0.5
    # Higher while the assistant is talking: with speakers rather than
    # headphones, some of its own voice gets past echo cancellation.
    barge_threshold: float = 0.8
    min_speech_ms: int = 200        # shorter is a cough or a click
    # A pause this long starts transcribing and retrieving, speculatively, so
    # both are done when the pause becomes long enough to end the turn. Speech
    # resuming throws the work away. 0 turns it off.
    speculate_silence_ms: int = 250
    eot_silence_ms: int = 500       # a pause this long may end the turn
    max_silence_ms: int = 1200      # a pause this long always does
    max_utterance_ms: int = 30_000
    preroll_ms: int = 300           # audio kept from before speech was detected
    barge_in_ms: int = 300          # speech over the assistant before it pauses
    backchannel_max_ms: int = 1000  # speech over it longer than this is a turn
    playback_grace_s: float = 5.0   # if the browser never says playback ended
    laya_end_of_turn: str = "shadow"
    laya_end_of_turn_below: float = 0.3  # live: below this p(complete), wait longer
    laya_barge_in: str = "shadow"
    laya_barge_in_above: float = 0.5     # live: at or above p(interrupt), stop

    def frames(self, ms: int) -> int:
        return max(1, ms // FRAME_MS)


class TurnBackend(Protocol):
    """A chat turn, in the three steps voice needs to hold apart."""

    async def prefetch(self, text: str) -> Any:
        """Start retrieving memory and documents for what is probably the
        message. Optional: a backend without it simply retrieves in prepare."""

    def discard(self, prefetched: Any) -> None:
        """Abandon a prefetch whose text was not, in the end, the message."""

    async def prepare(self, text: str, prefetched: Any = None) -> tuple[Any, dict]:
        """Store the person's message and gather memory, documents and web
        (reusing ``prefetched`` if it was for this text). Returns a handle and
        what to show (route, sources)."""

    def stream(self, handle: Any) -> AsyncIterator[str]:
        """The answer, as it is written."""

    async def finish(self, handle: Any, *, answer: str, interrupted: bool, voice: dict) -> dict:
        """Store the answer (only what was heard, if interrupted) and queue
        the turn for memory. Returns what to tell the browser."""

    async def log_decision(self, point: str, mode: str, state: dict,
                           decision: Decision | None, baseline: dict) -> None: ...


class Speech(Protocol):
    async def transcribe(self, pcm16: bytes) -> str: ...
    async def speak(self, text: str) -> bytes: ...


class Decider(Protocol):
    async def decide(self, state: dict[str, str], questions: dict[str, dict]) -> Decision | None: ...


Send = Callable[[dict | bytes], Awaitable[None]]


def _pcm16(frames: list[np.ndarray]) -> bytes:
    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


@dataclass
class _Answer:
    """The assistant's current turn, while it is being said."""

    index: int
    task: asyncio.Task | None = None
    sentences: list[str] = field(default_factory=list)
    heard_upto: int = -1     # the last sentence the browser started playing
    played_upto: int = -1    # the last one it finished
    synthesised: bool = False
    all_played: asyncio.Event = field(default_factory=asyncio.Event)
    audio_seconds: float = 0.0


class VoiceSession:
    def __init__(self, *, send: Send, speech: Speech, vad, turns: TurnBackend,
                 config: VoiceConfig | None = None, decisions: Decider | None = None) -> None:
        self.send = send
        self.speech = speech
        self.vad = vad
        self.turns = turns
        self.config = config or VoiceConfig()
        self.decisions = decisions

        c = self.config
        self._preroll: deque[np.ndarray] = deque(maxlen=c.frames(c.preroll_ms))
        self._carry = np.zeros(0, dtype=np.float32)
        # The person's utterance in progress; None while not hearing one.
        self._utterance: list[np.ndarray] | None = None
        self._speech_frames = 0
        self._silent_run = 0
        self._speech_ended_at = 0.0
        self._pending: asyncio.Task | None = None  # end-of-utterance check
        self._spec: asyncio.Task | None = None     # speculative transcript + retrieval
        # The assistant's turn; None while it is not answering.
        self._answer: _Answer | None = None
        self._turns = 0
        self._overlap: list[np.ndarray] = []
        self._overlap_speech = 0
        self._overlap_silence = 0
        self._judged_at = 0
        self._recent: deque[np.ndarray] = deque(maxlen=c.frames(c.preroll_ms))
        self._judging: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # ── Input ────────────────────────────────────────────────────────────────

    async def feed_audio(self, pcm16: bytes) -> None:
        samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        audio = np.concatenate([self._carry, samples])
        whole = len(audio) // FRAME * FRAME
        self._carry = audio[whole:]
        for start in range(0, whole, FRAME):
            await self._frame(audio[start:start + FRAME])

    async def on_message(self, message: dict) -> None:
        kind = message.get("type")
        answer = self._answer
        if kind == "playing" and answer is not None and message.get("turn") == answer.index:
            answer.heard_upto = max(answer.heard_upto, int(message.get("index", -1)))
        elif kind == "played" and answer is not None and message.get("turn") == answer.index:
            answer.heard_upto = max(answer.heard_upto, int(message.get("index", -1)))
            answer.played_upto = max(answer.played_upto, int(message.get("index", -1)))
            self._check_played(answer)
        elif kind == "interrupt":
            # The browser's own stop button.
            await self._interrupt("button")

    async def close(self) -> None:
        if self._answer is not None and self._answer.task is not None:
            self._answer.task.cancel()
        self._drop_spec()
        for task in [self._pending, self._judging, *self._tasks]:
            if task is not None:
                task.cancel()
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._report)
        return task

    @staticmethod
    def _report(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("voice task failed", exc_info=task.exception())

    # ── Hearing ──────────────────────────────────────────────────────────────

    async def _frame(self, frame: np.ndarray) -> None:
        c = self.config
        p = self.vad.probability(frame)

        if self._answer is not None and self._utterance is None:
            await self._over_the_assistant(frame, p >= c.barge_threshold)
            return

        speech = p >= c.speech_threshold
        if self._utterance is None:
            if speech:
                self._utterance = [*self._preroll, frame]
                self._preroll.clear()
                self._speech_frames, self._silent_run = 1, 0
                self._speech_ended_at = time.perf_counter()
                await self.send({"type": "vad", "speaking": True})
            else:
                self._preroll.append(frame)
            return

        self._utterance.append(frame)
        if speech:
            self._speech_frames += 1
            self._silent_run = 0
            self._speech_ended_at = time.perf_counter()
            if self._pending is not None:
                # They were only pausing: keep listening to the same utterance.
                self._pending.cancel()
                self._pending = None
            self._drop_spec()
            return

        self._silent_run += 1
        if (c.speculate_silence_ms and self._silent_run == c.frames(c.speculate_silence_ms)
                and self._spec is None and self._speech_frames >= c.frames(c.min_speech_ms)):
            self._spec = self._spawn(self._speculate(list(self._utterance)))
        too_long = len(self._utterance) >= c.frames(c.max_utterance_ms)
        if self._silent_run == c.frames(c.eot_silence_ms) or (too_long and self._pending is None):
            if self._speech_frames < c.frames(c.min_speech_ms):
                self._drop_spec()
                self._reset_utterance()
                await self.send({"type": "vad", "speaking": False})
                return
            await self.send({"type": "vad", "speaking": False})
            snapshot = list(self._utterance)
            self._pending = self._spawn(self._end_of_utterance(snapshot, self._speech_ended_at, forced=too_long))

    def _reset_utterance(self) -> None:
        self._utterance = None
        self._speech_frames = self._silent_run = 0

    def _drop_spec(self) -> None:
        spec, self._spec = self._spec, None
        if spec is None:
            return
        if spec.done() and not spec.cancelled() and spec.exception() is None:
            prefetched = spec.result()[2]
            if prefetched is not None and hasattr(self.turns, "discard"):
                self.turns.discard(prefetched)
        else:
            spec.cancel()

    async def _speculate(self, frames: list[np.ndarray]) -> tuple[str, int, Any]:
        """Transcribe what has been said so far and start retrieval for it.
        Used at the end of the turn only if nothing was said in between, in
        which case the audio since is silence and the transcript is the same."""
        started = time.perf_counter()
        text = (await self.speech.transcribe(_pcm16(frames))).strip()
        stt_ms = _ms(started)
        prefetched = None
        if text and hasattr(self.turns, "prefetch"):
            prefetched = await self.turns.prefetch(text)
        return text, stt_ms, prefetched

    async def _end_of_utterance(self, frames: list[np.ndarray], speech_ended_at: float, forced: bool) -> None:
        c = self.config
        spec, self._spec = self._spec, None
        prefetched = None
        if spec is not None and not forced:
            text, stt_ms, prefetched = await spec
            speculative = True
        else:
            if spec is not None:
                spec.cancel()
            started = time.perf_counter()
            text = (await self.speech.transcribe(_pcm16(frames))).strip()
            stt_ms, speculative = _ms(started), False
        if not text:
            if prefetched is not None and hasattr(self.turns, "discard"):
                self.turns.discard(prefetched)
            self._reset_utterance()
            self._pending = None
            return

        how = "vad"
        if self.decisions is not None and c.laya_end_of_turn == "shadow" and not forced:
            # Cannot change anything in shadow, so not waited for.
            self._spawn(self._shadow_end_of_turn(text))
        elif self.decisions is not None and c.laya_end_of_turn == "live" and not forced:
            decision = await self.decisions.decide({"transcript": text}, END_OF_TURN_QUESTIONS)
            complete = decision.p("utterance") if decision else None
            waits = complete is not None and complete < c.laya_end_of_turn_below
            self._spawn(self.turns.log_decision(
                "end_of_turn", c.laya_end_of_turn, {"transcript": text}, decision,
                {"ended": True, "silence_ms": c.eot_silence_ms}))
            if waits:
                # Sounds unfinished: give them the long pause before answering.
                # Speech resuming cancels this task, as at the short pause.
                how = "laya-wait"
                await asyncio.sleep((c.max_silence_ms - c.eot_silence_ms) / 1000)

        # Committed: from here on, speech is an interruption, not more of this.
        self._pending = None
        self._reset_utterance()
        self._turns += 1
        answer = _Answer(index=self._turns)
        self._answer = answer
        timing = {"stt_ms": stt_ms, "end_of_turn": how, "speech_end": speech_ended_at,
                  "audio_ms": len(frames) * FRAME_MS, "speculative": speculative}
        answer.task = self._spawn(self._respond(answer, text, timing, prefetched))

    async def _shadow_end_of_turn(self, text: str) -> None:
        decision = await self.decisions.decide({"transcript": text}, END_OF_TURN_QUESTIONS)
        await self.turns.log_decision("end_of_turn", "shadow", {"transcript": text}, decision,
                                      {"ended": True, "silence_ms": self.config.eot_silence_ms})

    # ── Answering ────────────────────────────────────────────────────────────

    async def _respond(self, answer: _Answer, text: str, timing: dict, prefetched: Any = None) -> None:
        await self.send({"type": "transcript", "turn": answer.index, "text": text})
        voice: dict = {"stt_ms": timing["stt_ms"], "end_of_turn": timing["end_of_turn"],
                       "utterance_ms": timing["audio_ms"], "speculative": timing["speculative"]}
        handle = prep = None
        parts: list[str] = []
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        synth: asyncio.Task | None = None
        try:
            # Shielded so an interruption during retrieval still leaves the
            # person's message stored, with an empty answer, as in text chat.
            prep = asyncio.ensure_future(self.turns.prepare(text, prefetched))
            handle, shown = await asyncio.shield(prep)
            await self.send({"type": "prepared", "turn": answer.index, **shown})
            synth = asyncio.create_task(self._synthesise(answer, sentences, timing, voice))
            splitter = SentenceSplitter()
            async for piece in self.turns.stream(handle):
                if not piece:
                    continue
                parts.append(piece)
                await self.send({"type": "token", "turn": answer.index, "text": piece})
                for sentence in splitter.feed(piece):
                    sentences.put_nowait(sentence)
            for sentence in splitter.flush():
                sentences.put_nowait(sentence)
            sentences.put_nowait(None)
            await synth
            answer.synthesised = True
            self._check_played(answer)
            try:
                await asyncio.wait_for(answer.all_played.wait(),
                                       answer.audio_seconds + self.config.playback_grace_s)
            except TimeoutError:
                logger.warning("browser never confirmed playback of turn %d", answer.index)
            stored = await self.turns.finish(handle, answer="".join(parts), interrupted=False, voice=voice)
            await self.send({"type": "done", "turn": answer.index, **stored})
        except asyncio.CancelledError:
            if synth is not None:
                synth.cancel()
            if handle is None and prep is not None:
                try:
                    handle, _ = await prep
                except Exception:
                    logger.exception("voice turn failed while preparing")
            if handle is not None:
                heard = " ".join(answer.sentences[: answer.heard_upto + 1])
                voice["interrupted_at_sentence"] = answer.heard_upto + 1
                voice["unspoken_chars"] = max(0, len("".join(parts)) - len(heard))
                try:
                    # Shielded: the cancellation that stopped the answer must
                    # not also lose the record of what was said.
                    stored = await asyncio.shield(
                        self.turns.finish(handle, answer=heard, interrupted=True, voice=voice))
                except Exception:
                    logger.exception("could not store an interrupted voice turn")
                else:
                    # So the thread shows what is stored -- what was heard --
                    # rather than everything that had streamed.
                    with contextlib.suppress(Exception):
                        await self.send({"type": "done", "turn": answer.index, "interrupted": True, **stored})
            raise
        except Exception as exc:
            logger.exception("voice turn failed")
            if synth is not None:
                synth.cancel()
            await self.send({"type": "error", "turn": answer.index, "detail": f"{exc.__class__.__name__}: {exc}"})
        finally:
            if self._answer is answer:
                self._answer = None
                self._overlap, self._overlap_speech, self._judged_at = [], 0, 0

    async def _synthesise(self, answer: _Answer, sentences: asyncio.Queue, timing: dict, voice: dict) -> None:
        while (sentence := await sentences.get()) is not None:
            started = time.perf_counter()
            audio = await self.speech.speak(sentence)
            if not answer.sentences:
                voice["tts_first_ms"] = _ms(started)
                # The number the voice target is set on: from the moment the
                # person stopped speaking to the first audio leaving for them.
                voice["first_audio_ms"] = _ms(timing["speech_end"])
            index = len(answer.sentences)
            answer.sentences.append(sentence)
            seconds = len(audio) / 2 / 24_000
            answer.audio_seconds += seconds
            await self.send({"type": "audio", "turn": answer.index, "index": index, "text": sentence,
                             "sample_rate": 24_000, "seconds": round(seconds, 3)})
            await self.send(audio)

    def _check_played(self, answer: _Answer) -> None:
        if answer.synthesised and answer.played_upto >= len(answer.sentences) - 1:
            answer.all_played.set()

    # ── Interruptions ────────────────────────────────────────────────────────

    async def _over_the_assistant(self, frame: np.ndarray, speech: bool) -> None:
        c = self.config
        if speech:
            if not self._overlap:
                # With the moment before it, as when listening: the onset of
                # the first word is quieter than the threshold, and without
                # it "Actually, stop" transcribes as "Yeah".
                self._overlap = list(self._recent)
            self._overlap.append(frame)
            self._overlap_speech += 1
            self._overlap_silence = 0
        else:
            self._recent.append(frame)
            if self._overlap:
                self._overlap.append(frame)
                self._overlap_silence += 1
                # A short gap does not end it; a long one does.
                if self._overlap_silence > c.frames(c.eot_silence_ms) and self._judging is None:
                    self._overlap, self._overlap_speech, self._judged_at = [], 0, 0
        if speech:
            self._recent.append(frame)
        if self._judging is None and self._overlap_speech >= self._judged_at + c.frames(c.barge_in_ms):
            # Judged again each time they keep talking past another window,
            # on everything said so far: a first guess on a third of a second
            # of audio is easily wrong.
            self._judged_at = self._overlap_speech
            self._judging = self._spawn(self._judge_overlap())

    async def _judge_overlap(self) -> None:
        c = self.config
        try:
            # Stop the sound at once; deciding whether it was a real
            # interruption takes a transcription, which they should not hear.
            started = time.perf_counter()
            await self.send({"type": "pause"})
            text = (await self.speech.transcribe(_pcm16(self._overlap))).strip()
            stt_ms = _ms(started)
            spoken_ms = self._overlap_speech * FRAME_MS
            # Nobody says "mm-hmm" for a second: past that it is a turn.
            interrupt = spoken_ms >= c.backchannel_max_ms or not is_backchannel(text)
            if self.decisions is not None and c.laya_barge_in == "shadow" and text:
                # Cannot change anything in shadow, so not waited for.
                self._spawn(self._shadow_barge_in(text, interrupt))
            elif self.decisions is not None and c.laya_barge_in == "live" and text:
                decision = await self.decisions.decide({"said": text}, BARGE_IN_QUESTIONS)
                self._spawn(self.turns.log_decision(
                    "barge_in", c.laya_barge_in, {"said": text}, decision, {"interrupt": interrupt}))
                p = decision.p("overlap") if decision else None
                if p is not None and spoken_ms < c.backchannel_max_ms:
                    interrupt = p >= c.laya_barge_in_above
            logger.info("barge-in heard=%r after %d ms of speech: %s (transcribed in %d ms)",
                        text, spoken_ms, "interrupt" if interrupt else "backchannel", stt_ms)
            if interrupt:
                await self._interrupt(text)
            else:
                # Kept, not cleared: if they carry on, the next judgement
                # hears all of it.
                await self.send({"type": "resume"})
        finally:
            self._judging = None

    async def _shadow_barge_in(self, text: str, interrupt: bool) -> None:
        decision = await self.decisions.decide({"said": text}, BARGE_IN_QUESTIONS)
        await self.turns.log_decision("barge_in", "shadow", {"said": text}, decision, {"interrupt": interrupt})

    async def _interrupt(self, said: str) -> None:
        answer = self._answer
        if answer is None:
            return
        self._answer = None
        if answer.task is not None:
            answer.task.cancel()
        await self.send({"type": "interrupted", "turn": answer.index})
        if self._overlap:
            # What they said over the assistant is the start of their next turn.
            self._utterance = self._overlap
            self._speech_frames = self._overlap_speech
            self._silent_run = 0
            self._speech_ended_at = time.perf_counter()
            await self.send({"type": "vad", "speaking": True})
        self._overlap, self._overlap_speech, self._judged_at = [], 0, 0
