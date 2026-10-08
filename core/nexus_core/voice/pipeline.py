"""One hands-free conversation, as a Pipecat pipeline.

    browser ──WebRTC──> input ─> speech-to-text ─> barge-in gate ─> user turn
                                                                      │
    browser <──WebRTC── output <─ text-to-speech <─ Nexus turn <──────┘
                          │
                          └─> heard: the sentences that actually played

Pipecat owns the parts that are hard to get right by hand: the WebRTC audio
path (with the browser's echo cancellation), Silero VAD, and Smart Turn v3,
which listens to *how* something was said to tell a finished sentence from a
pause mid-thought. Everything a turn means stays Nexus's: the Nexus turn step
hands each finished utterance to the same prepare/stream/finish functions as
typed chat (routers/voice.py's ChatTurns), so memory, documents and the web
apply, and the turn is stored as an ordinary message.

Interruptions. A turn starts the moment the VAD hears speech, but that never
stops the answer by itself: speech over the assistant is first judged by the
barge-in gate. A real interruption (a few words, or a second of sustained
speech) stops it at once; "mm-hmm" doesn't, and a turn that was only a
backchannel goes unanswered. An interrupted answer is stored as what was
heard: the sentences that had finished playing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.empty_user_turn import EmptyUserTurnConfig
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from ..web_client import WEB_TRIGGER, accepts, offers_web
from .runtime import RuntimeVoice
from .session import is_backchannel
from .speech_services import TTS_SAMPLE_RATE, RuntimeSTTService, RuntimeTTSService
from .text import SentenceSplitter

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# Said to stop the assistant rather than to ask it anything: worth stopping
# for, not worth answering.
STOP_COMMAND = re.compile(
    r"(?:(?:ok(?:ay)?|no|wait|hey|actually)[ ,]+)*"
    r"(?:stop|wait|hold on|hang on|enough|pause|cancel|shush|quiet|be quiet|shut up|"
    r"that'?s (?:enough|fine|ok(?:ay)?)|never ?mind|you can stop)"
    r"(?:[ ,]+(?:please|now|there|it|talking))*",
    re.IGNORECASE,
)


# Said the moment a spoken turn is going to search, so the few seconds the
# search takes aren't silence. Rotated, so it doesn't sound canned.
LOOKING_IT_UP = (
    "Give me a second, I'll look that up.",
    "One moment, let me check online.",
    "Let me look that up for you.",
)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", text).split())


def is_stop_command(text: str) -> bool:
    words = _words(text)
    return bool(words) and bool(STOP_COMMAND.fullmatch(words))


def not_worth_answering(text: str) -> bool:
    """Said over an answer only to acknowledge it or to stop it."""
    return is_backchannel(text) or is_stop_command(text)


# Words a finished thought doesn't end on. Smart Turn judges by how something
# was said; a sentence trailing off on one of these is unfinished whatever the
# pitch did.
DANGLING = frozenset(
    "the a an and or but so to of about with for in on at from into my your his her their our "
    "is are was were be because if that which than then like um uh er erm".split()
)


def ends_unfinished(text: str) -> bool:
    words = _words(text).lower().split()
    return bool(words) and words[-1] in DANGLING


@dataclass
class VoiceSettings:
    vad_confidence: float = 0.7
    vad_stop_ms: int = 200          # silence before Smart Turn is asked
    incomplete_wait_ms: int = 2000  # how long Smart Turn waits on "not finished yet"
    interrupt_min_words: int = 3    # words over an answer that stop it
    interrupt_after_ms: int = 1000  # or this much continuous speech over it
    # Speech this soon after a turn ended is the rest of that sentence: the
    # answer begun on its first half stops, and the whole is answered.
    continue_within_ms: int = 2000


@dataclass
class Conversation:
    """What the processors share about where the conversation is."""

    answering: bool = False  # from the transcript until the answer is stored
    turn: int = 0            # which answer, so an interruption is judged once each
    bot_speaking: bool = False
    turn_ended_at: float = 0.0  # when the person's last turn ended (time.time())
    continuing: bool = False    # their next turn finishes the last one


class SmartTurnReadingTheWords(TurnAnalyzerUserTurnStopStrategy):
    """Smart Turn, plus a look at the transcript: when the words trail off
    ("tell me about the...") the turn is held for the incomplete-sentence
    wait, and speech resuming carries on the same turn."""

    def __init__(self, *, hold_secs: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hold_secs = hold_secs
        self._hold: asyncio.Task | None = None

    async def _maybe_trigger_user_turn_stopped(self):
        if self._turn_complete and self._text and ends_unfinished(self._text):
            if self._hold is None:
                self._hold = self.task_manager.create_task(self._release_after_hold(), f"{self}::hold")
            return
        await super()._maybe_trigger_user_turn_stopped()

    async def _release_after_hold(self) -> None:
        try:
            await asyncio.sleep(self._hold_secs)
        except asyncio.CancelledError:
            return
        self._hold = None
        if self._turn_complete:
            await self.trigger_user_turn_stopped()

    async def _discard_pending_end_of_turn(self):
        await super()._discard_pending_end_of_turn()
        if self._hold is not None:
            await self.task_manager.cancel_task(self._hold)
            self._hold = None


class BargeInGate(FrameProcessor):
    """Decides when speech over the assistant is an interruption.

    Sits between speech-to-text and the user turn, where it sees every
    transcript and, broadcast upstream, the VAD and the bot's speaking state.
    """

    def __init__(self, conversation: Conversation, settings: VoiceSettings, **kwargs) -> None:
        super().__init__(**kwargs)
        self._conversation = conversation
        self._settings = settings
        self._sustained: asyncio.Task | None = None
        self._interrupted_turn = -1

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._cancel_sustained()
            since_turn = time.time() - self._conversation.turn_ended_at
            if self._can_interrupt and since_turn * 1000 < self._settings.continue_within_ms:
                # They hadn't finished: the turn detector cut them off.
                self._conversation.continuing = True
                await self._interrupt(f"speech resumed {since_turn:.2f}s after the turn ended")
            elif self._can_interrupt:
                self._sustained = self.create_task(self._interrupt_if_sustained())
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._cancel_sustained()
        elif isinstance(frame, TranscriptionFrame) and self._can_interrupt:
            words = len(_words(frame.text).split())
            if (words >= self._settings.interrupt_min_words and not is_backchannel(frame.text)) \
                    or is_stop_command(frame.text):
                await self._interrupt(f"heard {frame.text!r}")
        await self.push_frame(frame, direction)

    @property
    def _can_interrupt(self) -> bool:
        return self._conversation.answering and self._interrupted_turn != self._conversation.turn

    async def _interrupt_if_sustained(self) -> None:
        await asyncio.sleep(self._settings.interrupt_after_ms / 1000)
        if self._can_interrupt:
            self._sustained = None
            await self._interrupt("sustained speech")

    def _cancel_sustained(self) -> None:
        task, self._sustained = self._sustained, None
        if task is not None:
            task.cancel()

    async def _interrupt(self, why: str) -> None:
        self._interrupted_turn = self._conversation.turn
        logger.info("voice: interrupting the answer (%s)", why)
        await self.broadcast_interruption()

    async def cleanup(self):
        self._cancel_sustained()
        await super().cleanup()


class Spoken:
    """The answer's pieces in the order they will play, each with its length
    in seconds, as text-to-speech makes them."""

    def __init__(self) -> None:
        self.pieces: list[tuple[str, float]] = []
        self.filler: str | None = None  # said while searching; not part of the answer

    def add(self, text: str, seconds: float) -> None:
        if text:
            self.pieces.append((text, seconds))


class Heard(FrameProcessor):
    """After the output, where audio arrives as it plays: how much of the
    answer has been played, and so which pieces of it were heard. A piece
    counts once it has started playing -- someone who cut in halfway through
    a sentence heard it."""

    def __init__(self, conversation: Conversation, spoken: Spoken, **kwargs) -> None:
        super().__init__(**kwargs)
        self._conversation = conversation
        self._spoken = spoken
        self.played_seconds = 0.0
        self.played = asyncio.Event()

    def reset(self) -> None:
        self._spoken.pieces = []
        self._spoken.filler = None
        self.played_seconds = 0.0
        self.played = asyncio.Event()

    @property
    def sentences(self) -> list[str]:
        heard, start = [], 0.0
        for text, seconds in self._spoken.pieces:
            if start >= self.played_seconds:
                break
            if text != self._spoken.filler:
                heard.append(text)
            start += seconds
        return heard

    @property
    def text(self) -> str:
        return " ".join(self.sentences)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame) and frame.sample_rate:
            self.played_seconds += len(frame.audio) / 2 / frame.num_channels / frame.sample_rate
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.played.set()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._conversation.bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._conversation.bot_speaking = False
        await self.push_frame(frame, direction)


class NexusTurn(FrameProcessor):
    """Where the LLM would sit in a stock Pipecat bot: answers each finished
    utterance through Nexus's own turn (``turns`` is routers/voice.py's
    ChatTurns), streaming the answer on to text-to-speech."""

    # Longer than any answer takes to say; only reached if the output stalls.
    PLAYBACK_LIMIT_S = 300

    def __init__(self, turns, heard: Heard, conversation: Conversation, spoken: Spoken | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self._turns = turns
        self._heard = heard
        self._spoken = spoken
        self._conversation = conversation
        self._count = 0
        self._speech_ended_at = time.time()
        self._turn_ended_at = time.time()
        # For each finished utterance not yet answered: did it end while an
        # answer was still going (and so, not having stopped it, was not
        # meant as a turn)?
        self._said_over: list[bool] = []
        self._first_audio_at: float | None = None
        self._last_text = ""
        self._last_answer = ""  # as stored, to know whether it offered to check online
        self._fillers = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            text = _latest_user_text(frame.context)
            # Core keeps the conversation in the database; Pipecat's copy
            # would only grow.
            frame.context.set_messages([])
            said_over = self._said_over.pop(0) if self._said_over else False
            if not text:
                return
            if self._conversation.continuing:
                self._conversation.continuing = False
                text = f"{self._last_text} {text}".strip()
                said_over = False
            if said_over or (self._conversation.answering and not_worth_answering(text)):
                # Speech over an answer that the barge-in gate didn't take for
                # an interruption: "mm-hmm", or a misheard one ("Mamma.").
                logger.info("voice: not answering %r, said over the answer", text)
                return
            await self._respond(text)
            return
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speech_ended_at = frame.timestamp - frame.stop_secs
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._turn_ended_at = time.time()
            self._conversation.turn_ended_at = self._turn_ended_at
            self._said_over.append(self._conversation.answering)
        elif isinstance(frame, BotStartedSpeakingFrame) and self._first_audio_at is None:
            self._first_audio_at = time.time()
        await self.push_frame(frame, direction)

    async def _send(self, data: dict) -> None:
        with contextlib.suppress(Exception):
            await self.push_frame(RTVIServerMessageFrame(data=data))

    async def _respond(self, text: str) -> None:
        self._count += 1
        turn = self._count
        self._last_text = text
        stopped_at = self._speech_ended_at
        self._first_audio_at = None
        self._heard.reset()
        self._conversation.turn = turn
        self._conversation.answering = True
        voice: dict[str, Any] = {"pipeline": "pipecat", "end_of_turn": "smart-turn",
                                 "end_of_turn_ms": max(0, round((self._turn_ended_at - stopped_at) * 1000))}
        parts: list[str] = []
        handle = None
        prep = None
        await self._send({"type": "transcript", "turn": turn, "text": text})
        # The same test core applies when it prepares the turn: asked in
        # words, or yes to the last answer's offer to check online.
        searching = bool(WEB_TRIGGER.search(text)) or (accepts(text) and offers_web(self._last_answer))
        try:
            if searching:
                # Said while the search runs, not after: the answer follows it.
                filler = LOOKING_IT_UP[self._fillers % len(LOOKING_IT_UP)]
                self._fillers += 1
                if self._spoken is not None:
                    self._spoken.filler = filler
                voice["searched"] = True
                await self._send({"type": "searching", "turn": turn})
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame(filler + " "))
            # Shielded so an interruption during retrieval still leaves the
            # person's message stored, with an empty answer, as in text chat.
            prep = asyncio.ensure_future(self._turns.prepare(text))
            handle, shown = await asyncio.shield(prep)
            await self._send({"type": "prepared", "turn": turn, **shown})
            if not searching:
                await self.push_frame(LLMFullResponseStartFrame())
            splitter = SentenceSplitter()
            async for piece in self._turns.stream(handle):
                if not piece:
                    continue
                parts.append(piece)
                await self._send({"type": "token", "turn": turn, "text": piece})
                for sentence in splitter.feed(piece):
                    await self.push_frame(LLMTextFrame(sentence + " "))
            for sentence in splitter.flush():
                await self.push_frame(LLMTextFrame(sentence + " "))
            await self.push_frame(LLMFullResponseEndFrame())
            try:
                await asyncio.wait_for(self._heard.played.wait(), self.PLAYBACK_LIMIT_S)
            except TimeoutError:
                logger.warning("voice: turn %d never finished playing", turn)
            self._time(voice, stopped_at)
            stored = await self._turns.finish(handle, answer="".join(parts), interrupted=False, voice=voice)
            self._last_answer = _content(stored)
            await self._send({"type": "done", "turn": turn, **stored})
        except asyncio.CancelledError:
            # Interrupted, or the session ended: keep what was heard. No longer
            # answering from this moment, not once that is stored: what the
            # person goes on to say is their next turn, not talk over this one.
            self._conversation.answering = False
            await self._send({"type": "interrupted", "turn": turn})
            await asyncio.shield(self._store_interrupted(turn, prep, handle, "".join(parts), voice, stopped_at))
            raise
        except Exception as exc:
            logger.exception("voice turn failed")
            await self._send({"type": "error", "turn": turn, "detail": f"{exc.__class__.__name__}: {exc}"})
        finally:
            self._conversation.answering = False

    async def _store_interrupted(self, turn: int, prep, handle, written: str, voice: dict,
                                 stopped_at: float) -> None:
        if handle is None and prep is not None:
            try:
                handle, _ = await prep
            except Exception:
                logger.exception("voice turn failed while preparing")
                return
        if handle is None:
            return
        heard = self._heard.text
        voice["heard_pieces"] = len(self._heard.sentences)
        voice["unspoken_chars"] = max(0, len(written) - len(heard))
        self._time(voice, stopped_at)
        try:
            stored = await self._turns.finish(handle, answer=heard, interrupted=True, voice=voice)
        except Exception:
            logger.exception("could not store an interrupted voice turn")
            return
        self._last_answer = _content(stored)
        await self._send({"type": "done", "turn": turn, "interrupted": True, **stored})

    def _time(self, voice: dict, stopped_at: float) -> None:
        if self._first_audio_at is not None:
            # The number the voice target is set on: from the last of the
            # person's speech to the first audio leaving for them.
            voice["first_audio_ms"] = round((self._first_audio_at - stopped_at) * 1000)


def _content(stored: dict) -> str:
    message = stored.get("assistant_message") or {}
    return message.get("content", "") if isinstance(message, dict) else ""


def _latest_user_text(context: LLMContext) -> str:
    for message in reversed(context.get_messages()):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict)).strip()
    return ""


def build_pipeline(transport, *, speech: RuntimeVoice, turns, settings: VoiceSettings) -> Pipeline:
    conversation = Conversation()
    spoken = Spoken()
    heard = Heard(conversation, spoken)
    user = LLMUserAggregator(
        LLMContext(),
        params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                confidence=settings.vad_confidence, stop_secs=settings.vad_stop_ms / 1000)),
            user_turn_strategies=UserTurnStrategies(
                # A turn starts on speech, but starting one never stops the
                # answer: the barge-in gate decides that.
                start=[VADUserTurnStartStrategy(enable_interruptions=False),
                       TranscriptionUserTurnStartStrategy(enable_interruptions=False)],
                stop=[SmartTurnReadingTheWords(
                    hold_secs=settings.incomplete_wait_ms / 1000,
                    turn_analyzer=LocalSmartTurnAnalyzerV3(
                        params=SmartTurnParams(stop_secs=settings.incomplete_wait_ms / 1000)))],
            ),
            # A cough or a click is not a turn; nothing to say about it.
            empty_user_turn=EmptyUserTurnConfig(interrupted_prompt=None, idle_prompt=None),
        ),
    )
    return Pipeline([
        transport.input(),
        RuntimeSTTService(voice=speech),
        BargeInGate(conversation, settings),
        user,
        NexusTurn(turns, heard, conversation, spoken),
        RuntimeTTSService(voice=speech, on_piece=spoken.add),
        transport.output(),
        heard,
    ])


async def run_session(connection, *, app, chat_id, user_id, settings: VoiceSettings) -> None:
    """Run one voice session on an established WebRTC connection, until the
    browser disconnects. Called in the background by routers/voice_rtc.py."""
    from ..routers.chats import _live
    from ..routers.voice import ChatTurns
    from ..decisions import runtime_root

    root = runtime_root(app.state.settings.openai_base_url)
    speech = getattr(app.state, "voice_speech", None) or RuntimeVoice(app.state.http, root)
    holder = SimpleNamespace(app=app)  # what the chat helpers read: only .app
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True,
                               audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=TTS_SAMPLE_RATE),
    )
    pipeline = build_pipeline(transport, speech=speech, turns=ChatTurns(holder, chat_id, user_id),
                              settings=settings)
    worker = PipelineWorker(pipeline, params=PipelineParams(
        audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=TTS_SAMPLE_RATE))
    runner = WorkerRunner(handle_sigint=False)

    @worker.rtvi.event_handler("on_client_message")
    async def on_client_message(rtvi, message):
        if message.type == "interrupt":  # the stop button
            await rtvi.interrupt_bot()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    async with _live(holder):
        try:
            # The only model load voice waits for, before the first word and
            # never inside a turn.
            await speech.preload()
            await runner.add_workers(worker)
            await runner.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("voice session failed for chat=%s", chat_id)
