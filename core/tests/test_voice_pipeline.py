"""Voice over WebRTC: the Pipecat pipeline's Nexus parts (voice/pipeline.py).

Pipecat's own pieces (VAD, Smart Turn, the transport) are its to test. These
cover what Nexus adds: answering a finished utterance through the chat turn,
storing only what was heard when interrupted, the barge-in gate, the speech
services on the runtime, and who may open a session.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
    OutputAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from nexus_core.voice.pipeline import (
    BargeInGate,
    Conversation,
    LOOKING_IT_UP,
    Heard,
    NexusTurn,
    Spoken,
    VoiceSettings,
    ends_unfinished,
    is_backchannel,
    is_stop_command,
    not_worth_answering,
)
from nexus_core.main import app
from nexus_core.routers.voice_rtc import ChatTurns
from nexus_core.voice.speech_services import RuntimeSTTService, RuntimeTTSService
from nexus_core.voice.text import SentenceSplitter, clean_for_speech

# The seeded demo user (db/seed.sql), as in core/tests/conftest.py. Not
# imported from there: run from the repository root, "conftest" is the
# scenario suite's tests/conftest.py.
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class FakeTurns:
    """routers/voice_rtc.py's ChatTurns, without the database."""

    def __init__(self, answer=("The deadline is Friday. ", "It moved from Monday."), *,
                 heard=None, played: int = 0, block_after: int | None = None) -> None:
        self.answer = answer
        self.heard = heard          # a Heard to fill in as if sentences had played
        self.played = played        # how many of the sentences it plays
        self.block_after = block_after  # then the answer stalls, to be interrupted
        self.prepared: list[str] = []
        self.finished: list[dict] = []

    async def prepare(self, text, prefetched=None):
        self.prepared.append(text)
        return len(self.prepared), {"route": {"handler": "general"}}

    async def stream(self, handle):
        for i, piece in enumerate(self.answer):
            if self.block_after is not None and i == self.block_after and handle == 1:  # the first answer
                await asyncio.Event().wait()
            if i < self.played and self.heard is not None:
                # As if text-to-speech had said it and it had started playing.
                self.heard._spoken.add(piece.strip(), 1.0)
                self.heard.played_seconds = i + 0.5
            yield piece

    async def finish(self, handle, *, answer, interrupted, voice):
        self.finished.append({"answer": answer, "interrupted": interrupted, "voice": voice})
        return {"assistant_message": {"content": answer}}


def said(text: str) -> LLMContextFrame:
    context = LLMContext()
    context.set_messages([{"role": "user", "content": text}])
    return LLMContextFrame(context=context)


def turn_with(turns: FakeTurns, conversation: Conversation | None = None):
    conversation = conversation or Conversation()
    spoken = Spoken()
    heard = Heard(conversation, spoken)
    if turns.heard is True:
        turns.heard = heard
    return Pipeline([NexusTurn(turns, heard, conversation, spoken), heard]), conversation, heard


async def test_a_finished_utterance_is_answered_through_the_chat_turn():
    turns = FakeTurns()
    pipeline, conversation, _ = turn_with(turns)

    down, _ = await run_test(pipeline, frames_to_send=[said("When is the deadline?"), SleepFrame(sleep=0.2)])

    assert turns.prepared == ["When is the deadline?"]
    (stored,) = turns.finished
    assert stored["answer"] == "The deadline is Friday. It moved from Monday."
    assert stored["interrupted"] is False
    assert stored["voice"]["end_of_turn"] == "smart-turn"
    spoken = [f for f in down if isinstance(f, (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame))]
    assert [type(f) for f in spoken] == [LLMFullResponseStartFrame, LLMTextFrame, LLMTextFrame, LLMFullResponseEndFrame]
    told = [f.data["type"] for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert told == ["transcript", "prepared", "token", "token", "done"]
    assert not conversation.answering


async def test_an_interrupted_answer_is_stored_as_what_was_heard():
    turns = FakeTurns(answer=("The deadline is Friday. ", "It moved from Monday. ", "Good luck."),
                      heard=True, played=1, block_after=2)
    pipeline, conversation, _ = turn_with(turns)

    down, _ = await run_test(pipeline, frames_to_send=[
        said("When is the deadline?"), SleepFrame(sleep=0.1), InterruptionFrame(), SleepFrame(sleep=0.1)])

    assert len(turns.finished) == 1
    stored = turns.finished[0]
    assert stored["interrupted"] is True
    assert stored["answer"] == "The deadline is Friday."
    assert stored["voice"]["unspoken_chars"] > 0
    told = [f.data["type"] for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert "interrupted" in told and told[-1] == "done"
    assert not conversation.answering


async def test_speech_that_ends_during_the_answer_without_stopping_it_is_not_answered():
    """"Mm-hmm" over an answer comes back from speech-to-text as all sorts
    ("Mamma."): what counts is that it didn't interrupt."""
    turns = FakeTurns()
    conversation = Conversation(answering=True)
    pipeline, _, _ = turn_with(turns, conversation)

    started = conversation.turn_ended_at

    async def answer_ends():
        # Once the speech's end has been seen (with the answer still going),
        # not after a fixed 50 ms: with every package's tests loaded, the
        # pipeline took longer than that to start, and the answer "ended"
        # before the speech did.
        while conversation.turn_ended_at == started:
            await asyncio.sleep(0.005)
        conversation.answering = False

    task = asyncio.create_task(answer_ends())
    await run_test(pipeline, frames_to_send=[
        UserStoppedSpeakingFrame(), SleepFrame(sleep=0.1), said("Mamma."), SleepFrame(sleep=0.1)])
    await task
    assert turns.prepared == []


async def test_speech_that_outlasts_the_answer_is_answered():
    turns = FakeTurns()
    pipeline, _, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[UserStoppedSpeakingFrame(), said("Okay, and the dog?"),
                                             SleepFrame(sleep=0.2)])
    assert turns.prepared == ["Okay, and the dog?"]


async def test_the_same_words_after_the_answer_are_answered():
    """"Yes" is an answer to a question; only said over an answer is it noise."""
    turns = FakeTurns()
    pipeline, _, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[UserStoppedSpeakingFrame(), said("Yes."), SleepFrame(sleep=0.2)])
    assert turns.prepared == ["Yes."]


async def test_a_piece_counts_as_heard_once_it_starts_playing():
    spoken = Spoken()
    heard = Heard(Conversation(), spoken)
    for text in ("The deadline is Friday,", "though it moved from Monday.", "Good luck."):
        spoken.add(text, 0.01)  # 240 samples at 24 kHz each
    await run_test(heard, frames_to_send=[
        OutputAudioRawFrame(audio=bytes(2 * 240), sample_rate=24_000, num_channels=1),
        OutputAudioRawFrame(audio=bytes(2 * 48), sample_rate=24_000, num_channels=1)])
    assert heard.text == "The deadline is Friday, though it moved from Monday."


async def test_speaking_again_just_after_a_turn_ended_continues_it():
    processor, conversation = gate()
    conversation.turn_ended_at = time.time()
    down, up = await run_test(processor, frames_to_send=[VADUserStartedSpeakingFrame()])
    assert interruptions(down, up) and conversation.continuing_until > time.time()


async def test_a_continued_turn_is_answered_whole():
    turns = FakeTurns()
    pipeline, conversation, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[said("Tell me about the"), SleepFrame(sleep=0.2)])
    conversation.continuing_until = time.time() + 5
    await run_test(pipeline, frames_to_send=[said("keeper's dog."), SleepFrame(sleep=0.2)])
    assert turns.prepared == ["Tell me about the", "Tell me about the keeper's dog."]


async def test_a_continuation_that_never_came_does_not_merge_a_later_turn():
    turns = FakeTurns()
    pipeline, conversation, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[said("Who was the keeper?"), SleepFrame(sleep=0.2)])
    conversation.continuing_until = time.time() - 1  # it expired
    await run_test(pipeline, frames_to_send=[said("What about the dog?"), SleepFrame(sleep=0.2)])
    assert turns.prepared == ["Who was the keeper?", "What about the dog?"]


@pytest.mark.parametrize("text,unfinished", [
    ("Tell me about the", True), ("I was thinking that, um", True), ("Me and", True),
    ("Tell me about the keeper's dog.", False), ("What is the deadline?", False), ("", False),
    ("Look up who the prime minister of India is.", False), ("What's it about?", False),
    ("Where are you from", False), ("Is it the?", False),
    ("Who was the keeper at", True), ("Tell me more about", True), ("What's it about.", False),
])
def test_a_sentence_trailing_off_is_unfinished(text, unfinished):
    assert ends_unfinished(text) is unfinished


@pytest.mark.parametrize("text,stop", [
    ("Stop.", True), ("Okay, stop please", True), ("Hold on", True), ("You can stop.", True),
    ("Never mind", True), ("Stop the car at the next exit", False), ("What was the dog called?", False),
])
def test_stop_commands(text, stop):
    assert is_stop_command(text) is stop
    assert not_worth_answering(text) is stop


def test_backchannels_are_not_worth_answering():
    assert not_worth_answering("Mm-hmm.")
    assert not_worth_answering("Yeah, okay.")
    assert not not_worth_answering("Yeah, but what about Thursday?")


def gate(answering=True, **settings):
    conversation = Conversation(answering=answering, turn=1)
    return BargeInGate(conversation, VoiceSettings(**settings)), conversation


def interruptions(down, up):
    return [f for f in [*down, *up] if isinstance(f, InterruptionFrame)]


async def test_a_backchannel_does_not_interrupt():
    processor, _ = gate()
    down, up = await run_test(processor, frames_to_send=[
        VADUserStartedSpeakingFrame(), TranscriptionFrame("Mm-hmm.", "", ""),
        VADUserStoppedSpeakingFrame()])
    assert interruptions(down, up) == []


async def test_a_few_words_interrupt():
    processor, _ = gate()
    down, up = await run_test(processor, frames_to_send=[
        VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame(),
        TranscriptionFrame("Actually, what was the dog called?", "", "")])
    assert len(interruptions(down, up)) == 2  # one each way


async def test_stop_on_its_own_interrupts():
    processor, _ = gate()
    down, up = await run_test(processor, frames_to_send=[TranscriptionFrame("Stop.", "", "")])
    assert interruptions(down, up)


async def test_talking_over_it_for_long_enough_interrupts():
    processor, _ = gate(interrupt_after_ms=50)
    down, up = await run_test(processor, frames_to_send=[VADUserStartedSpeakingFrame(), SleepFrame(sleep=0.1)])
    assert interruptions(down, up)


async def test_a_short_sound_does_not():
    processor, _ = gate(interrupt_after_ms=100)
    down, up = await run_test(processor, frames_to_send=[
        VADUserStartedSpeakingFrame(), SleepFrame(sleep=0.03), VADUserStoppedSpeakingFrame(), SleepFrame(sleep=0.15)])
    assert interruptions(down, up) == []


async def test_nothing_is_interrupted_when_nothing_is_being_said():
    processor, _ = gate(answering=False)
    down, up = await run_test(processor, frames_to_send=[
        VADUserStartedSpeakingFrame(), TranscriptionFrame("Tell me about the lighthouse", "", "")])
    assert interruptions(down, up) == []


class FakeVoice:
    def __init__(self) -> None:
        self.transcribed: list[int] = []
        self.spoken: list[str] = []

    async def transcribe(self, pcm16: bytes) -> str:
        self.transcribed.append(len(pcm16))
        return " what is the deadline "

    async def speak(self, text: str) -> bytes:
        self.spoken.append(text)
        return bytes(2 * 24_000 // 10)  # 0.1 s


async def test_transcription_goes_to_the_runtime_as_raw_pcm():
    voice = FakeVoice()
    stt = RuntimeSTTService(voice=voice)
    assert stt.wants_wav_segments is False
    frames = [f async for f in stt.run_stt(bytes(3200))]
    assert voice.transcribed == [3200]
    assert [f.text for f in frames] == ["what is the deadline"]


async def test_each_sentence_is_spoken_by_the_runtime():
    voice = FakeVoice()
    down, _ = await run_test(RuntimeTTSService(voice=voice), frames_to_send=[
        LLMFullResponseStartFrame(), LLMTextFrame("The deadline is Friday. "),
        LLMTextFrame("It moved from Monday."), LLMFullResponseEndFrame(), SleepFrame(sleep=0.2)])
    assert voice.spoken == ["The deadline is Friday.", "It moved from Monday."]
    audio = [f for f in down if isinstance(f, TTSAudioRawFrame)]
    assert audio and all(f.sample_rate == 24_000 for f in audio)
    assert " ".join(f.text.strip() for f in down if isinstance(f, TTSTextFrame)) == " ".join(voice.spoken)


OFFER = {"sdp": "v=0", "type": "offer"}


async def test_opening_a_session_needs_a_login(client):
    r = await client.post(f"/voice/{uuid.uuid4()}/offer", json=OFFER)
    assert r.status_code == 401


async def test_only_your_own_chat(db_client):
    r = await db_client.post(f"/voice/{uuid.uuid4()}/offer", json=OFFER)
    assert r.status_code == 404


async def test_another_site_cannot_open_one(db_client):
    r = await db_client.post(f"/voice/{uuid.uuid4()}/offer", json=OFFER,
                             headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


async def test_candidates_only_for_your_own_connection(db_client):
    r = await db_client.patch(f"/voice/{uuid.uuid4()}/offer",
                              json={"pc_id": "someone-elses", "candidates": []})
    assert r.status_code == 404


async def test_what_is_said_after_an_interruption_is_answered():
    """The interrupted answer takes a moment to store; the question that cut
    it off must not count as talk over it meanwhile."""
    turns = FakeTurns(answer=("The deadline is Friday. ", "It moved."), heard=True, played=1, block_after=1)
    slow = turns.finish

    async def slow_finish(*args, **kwargs):
        await asyncio.sleep(0.15)
        return await slow(*args, **kwargs)

    turns.finish = slow_finish
    pipeline, conversation, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[
        said("When is the deadline?"), SleepFrame(sleep=0.05), InterruptionFrame(), SleepFrame(sleep=0.02),
        UserStoppedSpeakingFrame(), said("What was the dog called?"), SleepFrame(sleep=0.4)])
    assert turns.prepared == ["When is the deadline?", "What was the dog called?"]


async def test_a_turn_cut_off_before_its_answer_played_goes_with_the_next():
    """"What happened at the Singapore Grand Prix? ... Check it online." The
    second sentence stopped the first answer before any of it played; on its
    own it no longer says what to look up."""
    turns = FakeTurns(heard=True, played=0, block_after=0)
    pipeline, _, _ = turn_with(turns)
    await run_test(pipeline, frames_to_send=[
        said("What happened at the Singapore Grand Prix?"), SleepFrame(sleep=0.05), InterruptionFrame(),
        SleepFrame(sleep=0.05), UserStoppedSpeakingFrame(), said("Check it online."), SleepFrame(sleep=0.3),
        UserStoppedSpeakingFrame(), said("And the one before?"), SleepFrame(sleep=0.3)])
    assert turns.prepared == ["What happened at the Singapore Grand Prix?",
                              "What happened at the Singapore Grand Prix? Check it online.",
                              "And the one before?"]


def spoken_text(down) -> list[str]:
    return [f.text.strip() for f in down if isinstance(f, LLMTextFrame)]


async def test_asking_to_look_something_up_is_answered_with_a_word_first():
    turns = FakeTurns(answer=("It's Narendra Modi. ",))
    pipeline, _, _ = turn_with(turns)

    down, _ = await run_test(pipeline, frames_to_send=[said("Look up who the prime minister is"),
                                                       SleepFrame(sleep=0.2)])

    assert spoken_text(down) == [LOOKING_IT_UP[0], "It's Narendra Modi."]
    told = [f.data["type"] for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert told.index("searching") < told.index("prepared")
    (stored,) = turns.finished
    assert stored["answer"].strip() == "It's Narendra Modi." and stored["voice"]["searched"] is True


async def test_yes_to_checking_online_looks_it_up():
    turns = FakeTurns(answer=("Joe Biden, but that may be out of date. Want me to check online?",))
    pipeline, _, _ = turn_with(turns)
    down, _ = await run_test(pipeline, frames_to_send=[
        said("Who is the US president?"), SleepFrame(sleep=0.2), said("Yes please."), SleepFrame(sleep=0.2)])
    told = [f.data["type"] for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert told.count("searching") == 1
    assert any(t in LOOKING_IT_UP for t in spoken_text(down))


async def test_a_plain_question_has_no_filler():
    turns = FakeTurns(answer=("Friday.",))
    pipeline, _, _ = turn_with(turns)
    down, _ = await run_test(pipeline, frames_to_send=[said("When is the deadline?"), SleepFrame(sleep=0.2)])
    assert spoken_text(down) == ["Friday."]


async def test_the_filler_is_never_what_was_heard():
    spoken = Spoken()
    heard = Heard(Conversation(), spoken)
    spoken.filler = LOOKING_IT_UP[0]
    spoken.add(LOOKING_IT_UP[0], 0.01)
    spoken.add("It's Narendra Modi.", 0.01)
    heard.played_seconds = 0.015
    assert heard.text == "It's Narendra Modi."


# ── Saying things (voice/text.py) ────────────────────────────────────────────


def test_a_streamed_answer_is_cut_into_whole_sentences():
    splitter = SentenceSplitter()
    out = []
    for piece in ["The dead", "line is **Friday**. It ", "moved, e.g. from Mon", "day! Version 3.5 is ", "out"]:
        out += splitter.feed(piece)
    out += splitter.flush()
    assert out == ["The deadline is Friday.", "It moved, e.g. from Monday!", "Version 3.5 is out"]


def test_a_long_first_sentence_is_spoken_from_its_first_clause():
    splitter = SentenceSplitter()
    first = splitter.feed("Start every email with a subject line that says what you need, then keep")
    assert first == ["Start every email with a subject line that says what you need,"]
    # Only the first: later sentences wait to be whole.
    assert splitter.feed(" it short, because people skim, and end with one clear ask. Next") == [
        "then keep it short, because people skim, and end with one clear ask."]


def test_markdown_is_not_read_aloud():
    assert clean_for_speech("## Steps\n- run `uv sync` [docs](http://x) [1]") == "Steps run uv sync docs"
    assert clean_for_speech("There are 197 steps. [Source: kestrel-point.md]") == "There are 197 steps."


@pytest.mark.parametrize("text,expected", [
    ("mm-hmm", True), ("Yeah.", True), ("okay, right", True), ("", True),
    ("wait, stop", False), ("what about Tuesday", False), ("yes but why", False),
])
def test_backchannels(text, expected):
    assert is_backchannel(text) is expected


# ── The turn itself (routers/voice_rtc.py's ChatTurns) ───────────────────────


class StreamingProvider:
    name = "streaming"

    async def chat(self, messages, **kwargs):
        return "A Title"

    async def chat_stream(self, messages, **kwargs):
        for piece in ("Friday", "."):
            yield piece

    async def embed(self, texts, *, model=None):
        return [[0.0] for _ in texts]


async def test_a_voice_turn_is_an_ordinary_message_with_voice_timings(db_client):
    app.state.provider = StreamingProvider()
    chat = (await db_client.post("/chats", json={})).json()

    turns = ChatTurns(app, uuid.UUID(chat["id"]), DEMO_USER_ID)
    handle, shown = await turns.prepare("when is it due?")
    assert shown["user_message"]["content"] == "when is it due?"
    answer = "".join([p async for p in turns.stream(handle)])
    stored = await turns.finish(handle, answer=answer, interrupted=False, voice={"first_audio_ms": 900})
    assert stored["assistant_message"]["content"] == "Friday."

    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["voice"] == {"first_audio_ms": 900}
