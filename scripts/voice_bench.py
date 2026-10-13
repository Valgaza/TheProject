"""Voice benchmark: talk to Nexus over WebRTC the way the browser does, and time it.

Signs up a throwaway user, opens a voice session on core (POST
/voice/{chat}/offer, then a WebRTC peer connection) and plays the browser's
part: spoken questions (synthesised by the runtime's own TTS) are sent as a
microphone track in real time, the answer's audio is received, and the RTVI
messages on the data channel say what happened. Some exchanges talk over the
answer to measure interruption.

    uv run python scripts/voice_bench.py --exchanges 20 --out docs/benchmarks/2026-10-voice

Measures, per exchange:
  first_audio  end of the question's speech -> first answer audio. *Server*:
               from the end of speech as the VAD heard it to the audio
               leaving core (meta.voice.first_audio_ms; the eval target, p50
               < 1.5 s, p95 < 2.5 s). *Client*: from the end of the clip's
               audible speech to the answer's audio arriving here.
  transcript   what speech-to-text heard, against what was said
  interrupt    onset of speech over the answer -> the answer stopped

Then three checks of conversational behaviour (--no-checks to skip):
  hesitant     "Tell me about the ... keeper's dog": answered as one question
  backchannel  "mm-hmm" over an answer neither stops it nor gets a reply
  web          "Look up ..." is answered with a word first, then searched

Needs the stack up (./start.sh) on the MLX runtime. Cleans up after itself.
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import json
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import av
import httpx
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import ROOT, MemorySampler, _cleanup, _gb, _pct  # noqa: E402

SAMPLE_DOC = ROOT / "rag" / "sample_docs" / "kestrel-point.md"
ORIGIN = "http://localhost:3000"
RATE = 48_000          # what WebRTC carries
FRAME = RATE // 50     # 20 ms
LOUD = 0.02 * 32767    # what counts as audible

# (label, what is said). Cycled to the requested number of exchanges.
SCRIPT = [
    ("small-talk", "Hi, can you hear me okay?"),
    ("doc", "Who was the keeper at Kestrel Point?"),
    ("remember", "For this project, the deadline is the fourteenth of November."),
    ("general", "Give me one tip for writing clearer emails."),
    ("doc", "How many steps are there to the lamp room?"),
    ("recall", "When is the deadline for this project?"),
]
BARGE_IN = "Actually, stop there. Tell me about the dog instead."
LONG = "Tell me everything about how the lighthouse worked, in detail."
HESITANT = ("Tell me about the", "keeper's dog.")
BACKCHANNEL = "Mm-hmm."
WEB = "Look up who the prime minister of India is."


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def word_error_rate(said: str, heard: str) -> float:
    a, b = _words(said), _words(heard)
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return d[len(b)] / max(1, len(a))


async def synthesise(runtime: httpx.AsyncClient, text: str) -> np.ndarray:
    """The runtime's TTS (24 kHz), as 48 kHz PCM16 for the microphone track."""
    r = await runtime.post("/v1/audio/speech", json={"input": text}, timeout=120)
    r.raise_for_status()
    return np.repeat(np.frombuffer(r.content, dtype="<i2"), 2)


def speech_bounds(pcm: np.ndarray) -> tuple[float, float]:
    """Where the clip is audibly speech, in seconds: TTS pads both ends."""
    loud = np.nonzero(np.abs(pcm) > LOUD)[0]
    return (loud[0] / RATE, loud[-1] / RATE) if len(loud) else (0.0, len(pcm) / RATE)


class Microphone(MediaStreamTrack):
    """Real time: 20 ms of whatever has been said, silence otherwise."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: list[np.ndarray] = []
        self._buf = np.zeros(0, dtype="<i2")
        self._pts = 0
        self._started: float | None = None
        self.spoke_last_at = 0.0   # time.time() of the last audible frame sent

    def say(self, *parts: np.ndarray | float) -> None:
        """Queue speech; a float is that many seconds of silence."""
        for part in parts:
            self._queue.append(np.zeros(int(RATE * part), dtype="<i2") if isinstance(part, float) else part)

    @property
    def idle(self) -> bool:
        return not self._queue and len(self._buf) == 0

    async def recv(self) -> av.AudioFrame:
        if self._started is None:
            self._started = time.time()
        delay = self._started + self._pts / RATE - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        while len(self._buf) < FRAME and self._queue:
            self._buf = np.concatenate([self._buf, self._queue.pop(0)])
        if 0 < len(self._buf) < FRAME:  # the tail of the last clip
            self._buf = np.concatenate([self._buf, np.zeros(FRAME - len(self._buf), dtype="<i2")])
        if len(self._buf):
            chunk, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            if np.abs(chunk).max() > LOUD:
                self.spoke_last_at = time.time()
        else:
            chunk = np.zeros(FRAME, dtype="<i2")
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = RATE, self._pts, fractions.Fraction(1, RATE)
        self._pts += FRAME
        return frame


class Browser:
    """The microphone, the speakers and the data channel of one session.

    Every event is kept with the time it arrived (time.time()), so a caller
    asks for "the first X after t" rather than racing a queue: ``kind`` is an
    RTVI event (bot-ready, user-started-speaking, bot-started-speaking, ...),
    one of Nexus's server messages (transcript, prepared, searching,
    interrupted, done, error), or "audio" when answer audio becomes audible."""

    def __init__(self) -> None:
        self.mic = Microphone()
        self.pc = RTCPeerConnection()
        self.events: list[tuple[str, float, dict]] = []
        self._quiet_since = 0.0
        self._loud = False

    @classmethod
    async def connect(cls, http: httpx.AsyncClient, chat_id: str, core: str = "") -> "Browser":
        """Open a session, as the signed-in user ``http`` carries the cookie
        of; ``core`` is the base URL if ``http`` has none."""
        b = cls()
        b.pc.addTransceiver(b.mic, direction="sendrecv")
        channel = b.pc.createDataChannel("chat", ordered=True)

        @channel.on("open")
        def ready() -> None:
            channel.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": "1",
                                     "data": {"version": "1.0.0", "about": {"library": "voice_bench"}}}))

        @channel.on("message")
        def message(raw) -> None:
            b._receive(json.loads(raw))

        @b.pc.on("track")
        def track(t) -> None:
            asyncio.ensure_future(b._listen(t))

        await b.pc.setLocalDescription(await b.pc.createOffer())
        r = await http.post(f"{core}/voice/{chat_id}/offer", headers={"Origin": ORIGIN},
                            json={"sdp": b.pc.localDescription.sdp, "type": b.pc.localDescription.type})
        r.raise_for_status()
        answer = r.json()
        await b.pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        return b

    def _receive(self, m: dict) -> None:
        now = time.time()
        if m.get("type") == "server-message":
            data = m["data"]
            if data.get("type") != "token":
                self.events.append((data["type"], now, data))
        elif m.get("type"):
            self.events.append((m["type"], now, m.get("data") or {}))

    async def _listen(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            now = time.time()
            if np.abs(frame.to_ndarray()).max() > LOUD:
                if not self._loud and now - self._quiet_since > 0.6:
                    self.events.append(("audio", now, {}))
                self._loud = True
            else:
                if self._loud:
                    self._quiet_since = now
                self._loud = False

    def say(self, *parts) -> None:
        self.mic.say(*parts)

    async def said(self) -> float:
        """Wait until everything queued has gone out; when speech ended."""
        while not self.mic.idle:
            await asyncio.sleep(0.02)
        return self.mic.spoke_last_at

    def after(self, kind: str, t: float) -> list[tuple[float, dict]]:
        return [(at, m) for k, at, m in self.events if k == kind and at >= t]

    async def until(self, kind: str, after: float = 0.0, timeout: float = 60) -> tuple[float, dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for k, at, m in self.events:
                if at >= after and k == "error":
                    raise RuntimeError(m.get("detail"))
                if at >= after and k == kind:
                    return at, m
            await asyncio.sleep(0.02)
        raise TimeoutError(f"no {kind!r} within {timeout}s")

    async def close(self) -> None:
        await self.pc.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--core", default="http://localhost:8100")
    parser.add_argument("--exchanges", type=int, default=20)
    parser.add_argument("--barge-every", type=int, default=5, help="talk over every Nth answer")
    parser.add_argument("--no-checks", action="store_true", help="skip the conversational checks")
    parser.add_argument("--settle", type=float, default=30,
                        help="seconds to let the uploaded document's cataloguing finish first")
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    runtime_url = (env.get("OPENAI_BASE_URL") or "http://localhost:8003/v1").removesuffix("/v1")
    db = await asyncpg.connect(env["DATABASE_URL"])
    sampler = MemorySampler()
    results: list[dict] = []
    barges: list[dict] = []
    checks: list[dict] = []

    async with httpx.AsyncClient(base_url=args.core, timeout=60) as http, \
            httpx.AsyncClient(base_url=runtime_url, headers={"X-Service-Token": env["SERVICE_TOKEN"]}) as runtime:
        email = f"voicebench-{uuid.uuid4().hex[:12]}@example.com"
        user = (await http.post("/auth/signup", json={
            "email": email, "password": uuid.uuid4().hex, "display_name": "Voice Bench"})).raise_for_status().json()
        user_id, project_id = user["id"], None
        print(f"voice bench user {email}", flush=True)
        browser = None
        try:
            project_id = (await http.post("/projects", json={"name": "Voice bench"})).json()["id"]
            with SAMPLE_DOC.open("rb") as f:
                (await http.post("/documents", params={"project_id": project_id},
                                 files={"file": (SAMPLE_DOC.name, f, "text/markdown")}, timeout=600)).raise_for_status()
            chat_id = (await http.post(f"/projects/{project_id}/chats", json={})).json()["id"]

            print("synthesising the questions…", flush=True)
            texts = [t for _, t in SCRIPT] + [BARGE_IN, LONG, *HESITANT, BACKCHANNEL, WEB]
            audio = {t: await synthesise(runtime, t) for t in dict.fromkeys(texts)}
            if args.settle:
                print(f"letting the document's cataloguing finish ({args.settle:.0f}s)…", flush=True)
                await asyncio.sleep(args.settle)

            t0 = time.time()
            browser = await Browser.connect(http, chat_id)
            await browser.until("bot-ready", timeout=600)
            print(f"session ready in {time.time() - t0:.1f}s", flush=True)
            sampler.start()
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await asyncio.sleep(1.0)

            for n in range(args.exchanges):
                label, text = SCRIPT[n % len(SCRIPT)]
                asked = time.time()
                browser.say(audio[text])
                spoke_end = await browser.said()
                await browser.until("transcript", after=asked)
                first_at, _ = await browser.until("audio", after=spoke_end)
                row = {"n": n + 1, "label": label, "said": text,
                       "first_audio_ms": round((first_at - spoke_end) * 1000)}

                if args.barge_every and (n + 1) % args.barge_every == 0:
                    await asyncio.sleep(0.6)
                    lead, _ = speech_bounds(audio[BARGE_IN])
                    sent = time.time()
                    browser.say(audio[BARGE_IN])
                    onset = sent + lead
                    stopped_at, _ = await browser.until("interrupted", after=sent)
                    _, stored = await browser.until("done", after=stopped_at)
                    barges.append({"n": n + 1, "stop_ms": round((stopped_at - onset) * 1000),
                                   "stored": stored["assistant_message"]["content"]})
                    row["interrupted"] = True
                    # The question's own turn, as finally answered, is the
                    # last before the interruption; what was said over it is
                    # answered next.
                    before = [m["text"] for at, m in browser.after("transcript", asked) if at < stopped_at]
                    row["heard"] = before[-1] if before else ""
                    row["split"] = len(before) > 1
                    _, done = await _last_done(browser, stopped_at)
                    after_it = [m["text"] for _, m in browser.after("transcript", stopped_at)]
                    row["barge_heard"] = after_it[-1] if after_it else ""
                    done = (await _done_of_turn(browser, asked, stopped_at)) or done
                else:
                    _, done = await _last_done(browser, asked)
                    heard = [m["text"] for _, m in browser.after("transcript", asked)]
                    row["heard"] = heard[-1] if heard else ""
                    # Smart Turn ended the turn at a pause and the rest
                    # continued it: answered whole, but it cost a restart.
                    row["split"] = len(heard) > 1
                row["wer"] = round(word_error_rate(text, row["heard"]), 3)
                meta = done["assistant_message"].get("metadata", {})
                row["server"] = meta.get("voice", {})
                row["timings"] = meta.get("timings", {})
                row["route"] = meta.get("route")
                row["answer"] = done["assistant_message"]["content"][:160]
                results.append(row)
                print(f"  {row['n']:2d} {label:10s} first audio {row['first_audio_ms']/1000:5.2f}s "
                      f"(server {row['server'].get('first_audio_ms', 0)/1000:4.2f}s)  "
                      f"wer {row['wer']:.2f}{'  split' if row['split'] else ''}  heard {row['heard']!r}",
                      flush=True)
                # Past the window in which speaking again continues a turn.
                await asyncio.sleep(2.5)

            if not args.no_checks:
                print("conversational checks…", flush=True)
                checks.append(await check_hesitant(browser, audio))
                checks.append(await check_backchannel(browser, audio))
                checks.append(await check_web(browser, audio))
                for c in checks:
                    print(f"  {c['check']:12s} {'pass' if c['passed'] else 'FAIL'}  {c['detail']}", flush=True)
        finally:
            if browser is not None:
                await browser.close()
            await sampler.stop()
            print("cleaning up…", flush=True)
            await _cleanup(http, db, user_id, project_id)
            await db.close()

    firsts = [r["first_audio_ms"] for r in results]
    server = [r["server"]["first_audio_ms"] for r in results if r["server"].get("first_audio_ms")]
    report = {
        "started_at": started_at,
        "pipeline": "pipecat over WebRTC",
        "exchanges": len(results),
        "first_audio_ms": {"p50": _pct(firsts, 0.5), "p95": _pct(firsts, 0.95), "max": max(firsts),
                           "mean": round(statistics.fmean(firsts))},
        "server_first_audio_ms": {"p50": _pct(server, 0.5), "p95": _pct(server, 0.95), "max": max(server),
                                  "mean": round(statistics.fmean(server))} if server else None,
        "wer_mean": round(statistics.fmean(r["wer"] for r in results), 3),
        "split_turns": sum(1 for r in results if r.get("split")),
        "interruptions": barges,
        "checks": checks,
        "results": results,
        "memory": {"peaks": sampler.peaks, "min_available": sampler.min_available},
    }
    print(json.dumps({k: report[k] for k in ("server_first_audio_ms", "first_audio_ms", "wer_mean")}, indent=2))
    if args.out:
        out = Path(args.out)
        (out.parent / f"{out.name}.json").write_text(json.dumps(report, indent=2))
        (out.parent / f"{out.name}.md").write_text(_markdown(report))
        print(f"wrote {out}.json and {out}.md")
    return 0 if all(c["passed"] for c in checks) else 1


async def check_hesitant(browser: Browser, audio: dict) -> dict:
    """A pause mid-sentence: the whole sentence is what gets answered."""
    t = time.time()
    first, rest = HESITANT
    browser.say(audio[first], 0.7, audio[rest])
    await browser.said()
    await _last_done(browser, t)
    answered = [m["text"] for _, m in browser.after("transcript", t)]
    whole = answered[-1].lower() if answered else ""
    passed = whole.startswith("tell me about") and "dog" in whole
    return {"check": "hesitant", "passed": passed,
            "detail": f"answered {answered[-1] if answered else None!r}"
                      + (f" (after {answered[:-1]})" if len(answered) > 1 else ""),
            "transcripts": answered}


async def check_backchannel(browser: Browser, audio: dict) -> dict:
    """'Mm-hmm' over an answer: it carries on, and nothing replies to it."""
    t = time.time()
    browser.say(audio[LONG])
    await browser.said()
    started, _ = await browser.until("bot-started-speaking", after=t)
    await asyncio.sleep(1.5)
    said_at = time.time()
    browser.say(audio[BACKCHANNEL])
    await browser.said()
    await _last_done(browser, t)
    interrupted = bool(browser.after("interrupted", said_at))
    replied_to = [m["text"] for _, m in browser.after("transcript", said_at)]
    passed = not interrupted and not replied_to
    return {"check": "backchannel", "passed": passed,
            "detail": f"interrupted={interrupted}, answered after it={replied_to}"}


async def check_web(browser: Browser, audio: dict) -> dict:
    """A spoken request to look something up: a word first, then the search."""
    t = time.time()
    browser.say(audio[WEB])
    spoke_end = await browser.said()
    searching_at, _ = await browser.until("searching", after=t, timeout=30)
    first_at, _ = await browser.until("audio", after=spoke_end)
    done_at, done = await browser.until("done", after=searching_at, timeout=120)
    meta = done["assistant_message"].get("metadata", {})
    web = meta.get("web") or {}
    passed = meta.get("route") == "web" and bool(web.get("query_sent"))
    return {"check": "web", "passed": passed,
            "detail": f"first sound {first_at - spoke_end:.2f}s after asking, query sent "
                      f"{web.get('query_sent')!r}" + (f", error {web['error']!r}" if web.get("error") else ""),
            "first_sound_ms": round((first_at - spoke_end) * 1000),
            "answer": done["assistant_message"]["content"][:160]}


async def _done_of_turn(browser: Browser, asked: float, before: float) -> dict | None:
    """The stored answer of the last turn begun after ``asked`` and before ``before``."""
    dones = [m for at, m in browser.after("done", asked) if at <= before + 0.5]
    return dones[-1] if dones else None


async def _last_done(browser: Browser, t: float) -> tuple[float, dict]:
    """The 'done' of the last turn begun after t, once nothing more follows."""
    while True:
        at, done = await browser.until("done", after=t, timeout=120)
        await asyncio.sleep(3.0)
        later = browser.after("transcript", at)
        if not later:
            return at, done
        t = later[-1][0]


def _markdown(r: dict) -> str:
    f = r["first_audio_ms"]
    sv = r["server_first_audio_ms"] or {}
    lines = ["# Voice benchmark", "", f"{r['started_at']} · {r['exchanges']} exchanges · {r['pipeline']}", "",
             "End of speech to the first answer audio. *Server*: from the end of speech as voice "
             "detection heard it to the audio leaving core (the target's measure); it includes Smart "
             "Turn's judgement that the person had finished. *Client*: from the end of the question "
             "clip's audible speech to the answer's audio arriving over WebRTC.", "",
             "| Measure | p50 | p95 | max | mean |", "|---|---|---|---|---|",
             f"| Server | {sv.get('p50', 0)/1000:.2f}s | {sv.get('p95', 0)/1000:.2f}s | "
             f"{sv.get('max', 0)/1000:.2f}s | {sv.get('mean', 0)/1000:.2f}s |",
             f"| Client | {f['p50']/1000:.2f}s | {f['p95']/1000:.2f}s | {f['max']/1000:.2f}s | {f['mean']/1000:.2f}s |", "",
             f"Mean word error rate of the transcripts: {r['wer_mean']:.1%}.", ""]
    if r["interruptions"]:
        lines += ["## Interruptions", "", "From the onset of speech over the answer to the answer stopping. "
                  "It stops on a few words, a stop phrase, or a second of continuous speech; \"mm-hmm\" "
                  "never stops it.", "", "| Exchange | Stopped after | Stored as heard |", "|---|---|---|"]
        for b in r["interruptions"]:
            lines.append(f"| {b['n']} | {b['stop_ms']} ms | {b['stored'][:80]!r} |")
        lines.append("")
    if r["checks"]:
        lines += ["## Conversational checks", "", "| Check | Result | Detail |", "|---|---|---|"]
        for c in r["checks"]:
            lines.append(f"| {c['check']} | {'pass' if c['passed'] else '**fail**'} | {c['detail']} |")
        lines.append("")
    split = sum(1 for x in r["results"] if x.get("split"))
    lines += ["## Every exchange", "", f"*Split*: Smart Turn ended the turn at a pause mid-sentence and the "
              f"rest continued it; answered whole, at the cost of a restart ({split} of {len(r['results'])}).", "",
              "| # | Kind | First audio (server) | End of turn | First token | WER | Heard |",
              "|---|---|---|---|---|---|---|"]
    for x in r["results"]:
        s = x["server"]
        kind = x["label"] + (" (interrupted)" if x.get("interrupted") else "") + (" (split)" if x.get("split") else "")
        lines.append(f"| {x['n']} | {kind} | "
                     f"{s.get('first_audio_ms', 0)/1000:.2f}s | {s.get('end_of_turn_ms', '–')} ms | "
                     f"{x.get('timings', {}).get('first_token_ms', '–')} ms | {x['wer']:.2f} | {x['heard']} |")
    lines += ["", "## Peak memory", "", "| Process | Peak |", "|---|---|"]
    for name, size in sorted(r["memory"]["peaks"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {_gb(size)} |")
    if r["memory"]["min_available"]:
        lines += ["", f"Lowest available system memory: {_gb(r['memory']['min_available'])}.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
