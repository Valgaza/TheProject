"""Hands-free voice: hearing a turn end, answering aloud, being interrupted.

The session is driven with synthetic audio (a tone is speech, zeros are
silence, judged by the loudness VAD) and fake speech models, fed in 32 ms
chunks the way a browser streams, so every timing rule runs as it would live.
"""

from __future__ import annotations

import asyncio
import json
import types
import uuid

import numpy as np
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nexus_core.auth import SESSION_COOKIE_NAME, create_session_token
from nexus_core.decisions import Decision
from nexus_core.main import app
from nexus_core.routers.voice import ChatTurns
from nexus_core.voice.session import VoiceConfig, VoiceSession, is_backchannel
from nexus_core.voice.text import SentenceSplitter, clean_for_speech
from nexus_core.voice.vad import FRAME, SAMPLE_RATE, EnergyVAD

from conftest import DEMO_USER_ID


def tone(ms: int) -> bytes:
    t = np.arange(SAMPLE_RATE * ms // 1000) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 220 * t) * 0.3 * 32767).astype("<i2").tobytes()


def silence(ms: int) -> bytes:
    return bytes(2 * (SAMPLE_RATE * ms // 1000))


class FakeSpeech:
    def __init__(self, heard=("what is the deadline",)) -> None:
        self.heard = list(heard)
        self.transcribed: list[int] = []
        self.spoken: list[str] = []

    async def transcribe(self, pcm16: bytes) -> str:
        self.transcribed.append(len(pcm16))
        return self.heard.pop(0) if len(self.heard) > 1 else self.heard[0]

    async def speak(self, text: str) -> bytes:
        self.spoken.append(text)
        return bytes(2 * 24_000 // 10)  # 0.1 s


class FakeTurns:
    def __init__(self, answer=("The deadline is Friday. ", "It moved from Monday.")) -> None:
        self.answer = answer
        self.prepared: list[str] = []
        self.finished: list[dict] = []
        self.logged: list[tuple] = []
        self.prefetched: list[str] = []
        self.discarded: list[str] = []
        self.used_prefetch: list = []
        self.hold = asyncio.Event()
        self.hold.set()

    async def prefetch(self, text):
        self.prefetched.append(text)
        return {"text": text}

    def discard(self, prefetched):
        self.discarded.append(prefetched["text"])

    async def prepare(self, text, prefetched=None):
        self.prepared.append(text)
        self.used_prefetch.append(prefetched)
        return len(self.prepared), {"route": {"handler": "general"}}

    async def stream(self, handle):
        for piece in self.answer:
            await self.hold.wait()
            yield piece

    async def finish(self, handle, *, answer, interrupted, voice):
        self.finished.append({"answer": answer, "interrupted": interrupted, "voice": voice})
        return {"assistant_message": {"content": answer}}

    async def log_decision(self, point, mode, state, decision, baseline):
        self.logged.append((point, mode, state, baseline))


class FakeDecisions:
    def __init__(self, **p) -> None:
        self.p = p

    async def decide(self, state, questions):
        (name,) = questions
        yes = {"utterance": "complete", "overlap": "interrupt"}[name]
        no = {"utterance": "unfinished", "overlap": "backchannel"}[name]
        p = self.p.get(name, 0.9)
        return Decision(answers={name: {"probs": {yes: p, no: 1 - p}}}, ms=5)


class Browser:
    """Plays audio the moment it arrives and says so, like the frontend."""

    def __init__(self, ack: bool = True) -> None:
        self.messages: list[dict] = []
        self.audio: list[bytes] = []
        self.ack = ack
        self.session: VoiceSession | None = None

    async def send(self, message) -> None:
        if isinstance(message, bytes):
            self.audio.append(message)
            return
        self.messages.append(message)
        if message["type"] == "audio" and self.ack:
            await self.session.on_message({"type": "playing", "turn": message["turn"], "index": message["index"]})
            await self.session.on_message({"type": "played", "turn": message["turn"], "index": message["index"]})

    def kinds(self) -> list[str]:
        return [m["type"] for m in self.messages if m["type"] != "token"]


def make(speech=None, turns=None, decisions=None, ack=True, **config):
    browser = Browser(ack=ack)
    session = VoiceSession(send=browser.send, speech=speech or FakeSpeech(), vad=EnergyVAD(),
                           turns=turns or FakeTurns(), config=VoiceConfig(**config), decisions=decisions)
    browser.session = session
    return session, browser


async def speak(session: VoiceSession, *chunks: bytes) -> None:
    """Stream audio in 32 ms pieces, letting the session's tasks run between."""
    for chunk in chunks:
        for i in range(0, len(chunk), FRAME * 2):
            await session.feed_audio(chunk[i:i + FRAME * 2])
            await asyncio.sleep(0)


async def settle(session: VoiceSession) -> None:
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not session._tasks:
            return


# ── Saying things ────────────────────────────────────────────────────────────


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


# ── Taking turns ─────────────────────────────────────────────────────────────


async def test_a_pause_after_speech_ends_the_turn_and_the_answer_is_spoken():
    session, browser = make()
    await speak(session, silence(300), tone(800), silence(600))
    await settle(session)

    turns: FakeTurns = session.turns
    assert turns.prepared == ["what is the deadline"]
    assert session.speech.spoken == ["The deadline is Friday.", "It moved from Monday."]
    assert browser.kinds() == ["vad", "vad", "transcript", "prepared", "audio", "audio", "done"]
    assert len(browser.audio) == 2
    stored = turns.finished[0]
    assert stored["answer"] == "The deadline is Friday. It moved from Monday." and not stored["interrupted"]
    assert stored["voice"]["first_audio_ms"] >= 0 and stored["voice"]["end_of_turn"] == "vad"


async def test_a_short_pause_mid_sentence_does_not_end_the_turn():
    session, _ = make()
    await speak(session, tone(500), silence(300), tone(500))
    await settle(session)
    # The pause was long enough to start guessing, not to answer; the guess
    # is thrown away when they carry on.
    assert session.turns.prepared == []
    assert session.turns.prefetched == ["what is the deadline"]
    assert session.turns.discarded == ["what is the deadline"]


async def test_the_turn_uses_what_was_fetched_during_the_pause():
    session, _ = make()
    await speak(session, tone(500), silence(600))
    await settle(session)
    turns = session.turns
    assert len(session.speech.transcribed) == 1  # once, during the pause
    assert turns.prepared == ["what is the deadline"]
    assert turns.used_prefetch == [{"text": "what is the deadline"}]
    assert turns.finished[0]["voice"]["speculative"] is True


async def test_a_click_is_not_a_turn():
    session, browser = make()
    await speak(session, tone(64), silence(700))
    await settle(session)
    assert session.speech.transcribed == [] and session.turns.prepared == []


async def test_speech_resuming_after_the_short_pause_continues_the_same_turn():
    """Live end-of-turn: an unfinished-sounding transcript waits for the long
    pause, and speech within it joins the same utterance."""
    speech = FakeSpeech(heard=("so the thing is", "so the thing is the deadline moved"))
    session, _ = make(speech=speech, decisions=FakeDecisions(utterance=0.1),
                      laya_end_of_turn="live", max_silence_ms=900)
    await speak(session, tone(500), silence(550))
    await asyncio.sleep(0.05)   # inside the wait for the long pause
    await speak(session, tone(500), silence(550))
    await settle(session)
    await asyncio.sleep(0.5)
    await settle(session)
    assert session.turns.prepared == ["so the thing is the deadline moved"]


async def test_shadow_end_of_turn_is_logged_and_the_rules_act():
    session, _ = make(decisions=FakeDecisions(utterance=0.05), laya_end_of_turn="shadow")
    await speak(session, tone(500), silence(600))
    await settle(session)
    assert session.turns.prepared == ["what is the deadline"]
    point, mode, state, baseline = session.turns.logged[0]
    assert (point, mode, state) == ("end_of_turn", "shadow", {"transcript": "what is the deadline"})


# ── Interruptions ────────────────────────────────────────────────────────────


async def test_talking_over_the_answer_stops_it_and_keeps_only_what_was_heard():
    speech = FakeSpeech(heard=("what is the deadline", "no wait, the other project", "the other project"))
    turns = FakeTurns()
    session, browser = make(speech=speech, turns=turns, ack=False)
    await speak(session, tone(500), silence(600))
    await settle(session)
    # The first sentence is playing, the second is queued, playback unconfirmed.
    await session.on_message({"type": "playing", "turn": 1, "index": 0})
    await speak(session, tone(400))
    await settle(session)

    assert "pause" in browser.kinds() and "interrupted" in browser.kinds()
    assert turns.finished[0] == {"answer": "The deadline is Friday.", "interrupted": True,
                                 "voice": turns.finished[0]["voice"]}
    assert turns.finished[0]["voice"]["interrupted_at_sentence"] == 1

    # What they said over it is the start of the next turn.
    await speak(session, silence(600))
    await settle(session)
    assert turns.prepared[-1] == "the other project"


async def test_mm_hmm_over_the_answer_is_not_an_interruption():
    speech = FakeSpeech(heard=("what is the deadline", "mm-hmm"))
    session, browser = make(speech=speech, ack=False)
    await speak(session, tone(500), silence(600))
    await settle(session)
    await speak(session, tone(400), silence(100))
    await settle(session)
    assert browser.kinds()[-2:] == ["pause", "resume"]
    assert "interrupted" not in browser.kinds()
    assert session.turns.finished == []  # still waiting for playback to end


async def test_talking_on_after_a_misheard_backchannel_still_interrupts():
    """The first third of a second of "Actually, stop there" can transcribe as
    "Yeah". If they keep talking it is judged again, on all of it, and past a
    second of speech it is a turn whatever it sounded like."""
    speech = FakeSpeech(heard=("what is the deadline", "Yeah.", "Yeah.", "Yeah.", "Yeah."))
    session, browser = make(speech=speech, ack=False)
    await speak(session, tone(500), silence(600))
    await settle(session)
    await speak(session, tone(1200))
    await settle(session)
    assert browser.kinds().count("pause") >= 2 and "interrupted" in browser.kinds()


async def test_live_barge_in_lets_laya_overrule_the_backchannel_list():
    speech = FakeSpeech(heard=("what is the deadline", "okay"))
    session, browser = make(speech=speech, ack=False, decisions=FakeDecisions(overlap=0.95),
                            laya_barge_in="live", laya_end_of_turn="off")
    await speak(session, tone(500), silence(600))
    await settle(session)
    await speak(session, tone(400))
    await settle(session)
    assert "interrupted" in browser.kinds()


async def test_closing_mid_answer_stores_what_was_heard():
    turns = FakeTurns()
    turns.hold.clear()  # the model stalls after preparing
    session, _ = make(turns=turns)
    await speak(session, tone(500), silence(600))
    await settle(session)
    await session.close()
    assert turns.finished == [{"answer": "", "interrupted": True, "voice": turns.finished[0]["voice"]}]


# ── The real turn, on the database ───────────────────────────────────────────


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


    turns = ChatTurns(types.SimpleNamespace(app=app), uuid.UUID(chat["id"]), DEMO_USER_ID)
    app.state.settings.rag_min_score = 0.5
    handle, shown = await turns.prepare("when is it due?", await turns.prefetch("when is it due?"))
    assert handle.prepared.timings.get("prefetched") == 1
    assert shown["user_message"]["content"] == "when is it due?"
    answer = "".join([p async for p in turns.stream(handle)])
    stored = await turns.finish(handle, answer=answer, interrupted=False, voice={"first_audio_ms": 900})
    assert stored["assistant_message"]["content"] == "Friday."

    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["metadata"]["voice"] == {"first_audio_ms": 900}


# ── The socket ───────────────────────────────────────────────────────────────


def test_another_origin_cannot_open_a_voice_socket(client):
    tc = TestClient(app)
    tc.cookies.set(SESSION_COOKIE_NAME, create_session_token(DEMO_USER_ID, app.state.settings))
    with pytest.raises(WebSocketDisconnect) as closed:
        with tc.websocket_connect(f"/ws/voice/{DEMO_USER_ID}", headers={"origin": "https://evil.example"}):
            pass
    assert closed.value.code == 1008


def test_no_session_means_no_voice(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(app).websocket_connect(f"/ws/voice/{DEMO_USER_ID}",
                                               headers={"origin": "http://localhost:3000"}):
            pass
    assert closed.value.code == 1008
