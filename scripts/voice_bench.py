"""Voice benchmark: talk to Nexus the way the browser does, and time it.

Opens WS /ws/voice/{chat} on core as a signed-in throwaway user, and plays the
part of the browser: a spoken question (synthesised by the runtime's own TTS,
resampled to 16 kHz) is streamed in real time followed by silence, the
answer's audio is "played" against a simulated clock, and playback is
acknowledged sentence by sentence as the frontend does. Some exchanges talk
over the answer to measure barge-in.

    uv run python scripts/voice_bench.py --exchanges 20 --out docs/benchmarks/2026-10-voice

Measures, per exchange:
  first_audio  end of the question's audio -> first answer audio received
               (the eval target: p50 < 1.5 s, p95 < 2.5 s). It includes the
               end-of-turn silence window, as a person would experience it.
  transcript   what speech-to-text heard, against what was said
  barge-in     speech onset over the answer -> playback paused, and -> stopped

Needs the stack up (./start.sh) on the MLX runtime. Cleans up after itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
import numpy as np
import websockets
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import ROOT, MemorySampler, _cleanup, _gb, _pct  # noqa: E402

SAMPLE_DOC = ROOT / "rag" / "sample_docs" / "kestrel-point.md"
ORIGIN = "http://localhost:3000"
CHUNK = 512  # samples per frame sent: 32 ms at 16 kHz, as the browser worklet

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


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def leading_silence(pcm16: bytes) -> float:
    """Seconds before the clip is audibly speech: TTS pads its start."""
    audio = np.abs(np.frombuffer(pcm16, dtype="<i2"))
    loud = np.nonzero(audio > 0.02 * 32767)[0]
    return loud[0] / 16_000 if len(loud) else 0.0


def word_error_rate(said: str, heard: str) -> float:
    a, b = _words(said), _words(heard)
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return d[len(b)] / max(1, len(a))


async def synthesise(runtime: httpx.AsyncClient, text: str) -> bytes:
    """The runtime's TTS at 24 kHz, resampled to the 16 kHz the socket takes."""
    r = await runtime.post("/v1/audio/speech", json={"input": text}, timeout=120)
    r.raise_for_status()
    audio = np.frombuffer(r.content, dtype="<i2").astype(np.float32)
    n = int(len(audio) * 16_000 / 24_000)
    resampled = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return resampled.astype("<i2").tobytes()


class Browser:
    """The microphone, the speakers and the socket."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.outbox: list[bytes] = []          # audio queued to "speak"
        self.spoke_last_at = 0.0               # when the last queued audio went out
        self.events: asyncio.Queue = asyncio.Queue()
        self.playhead = 0.0
        self.header: dict | None = None
        self.timers: list[asyncio.TimerHandle] = []

    def say(self, pcm16: bytes) -> None:
        self.outbox += [pcm16[i:i + CHUNK * 2] for i in range(0, len(pcm16), CHUNK * 2)]

    async def microphone(self) -> None:
        """Real time: one 32 ms frame every 32 ms, silence when not speaking."""
        silence = bytes(CHUNK * 2)
        next_at = time.perf_counter()
        while True:
            frame = self.outbox.pop(0) if self.outbox else silence
            await self.ws.send(frame)
            if frame is not silence and not self.outbox:
                self.spoke_last_at = time.perf_counter()
            next_at += CHUNK / 16_000
            await asyncio.sleep(max(0, next_at - time.perf_counter()))

    async def speakers(self) -> None:
        loop = asyncio.get_running_loop()
        async for message in self.ws:
            now = time.perf_counter()
            if isinstance(message, bytes):
                h, self.header = self.header, None
                start = max(now, self.playhead)
                self.playhead = start + h["seconds"]
                ack = {"turn": h["turn"], "index": h["index"]}
                self.timers.append(loop.call_later(start - now, self._send, {"type": "playing", **ack}))
                self.timers.append(loop.call_later(self.playhead - now, self._send, {"type": "played", **ack}))
                await self.events.put(("audio", now, h))
                continue
            m = json.loads(message)
            if m["type"] == "audio":
                self.header = m
            elif m["type"] == "interrupted":
                for t in self.timers:
                    t.cancel()
                self.timers, self.playhead = [], 0.0
                await self.events.put((m["type"], now, m))
            elif m["type"] == "pause":
                await self.events.put(("pause", now, m))
            elif m["type"] != "token":
                await self.events.put((m["type"], now, m))

    def _send(self, message: dict) -> None:
        asyncio.ensure_future(self.ws.send(json.dumps(message)))

    async def until(self, kind: str, timeout: float = 60, turn: int | None = None):
        deadline = time.perf_counter() + timeout
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise TimeoutError(f"no {kind!r} within {timeout}s")
            k, at, m = await asyncio.wait_for(self.events.get(), left)
            if k == kind and (turn is None or m.get("turn") == turn):
                return at, m
            if k == "error":
                raise RuntimeError(m.get("detail"))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--core", default="http://localhost:8100")
    parser.add_argument("--exchanges", type=int, default=20)
    parser.add_argument("--barge-every", type=int, default=5, help="talk over every Nth answer")
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args()

    env = dotenv_values(ROOT / ".env")
    runtime_url = (env.get("OPENAI_BASE_URL") or "http://localhost:8003/v1").removesuffix("/v1")
    db = await asyncpg.connect(env["DATABASE_URL"])
    sampler = MemorySampler()
    results: list[dict] = []
    barges: list[dict] = []

    async with httpx.AsyncClient(base_url=args.core, timeout=60) as http, \
            httpx.AsyncClient(base_url=runtime_url, headers={"X-Service-Token": env["SERVICE_TOKEN"]}) as runtime:
        email = f"voicebench-{uuid.uuid4().hex[:12]}@example.com"
        user = (await http.post("/auth/signup", json={
            "email": email, "password": uuid.uuid4().hex, "display_name": "Voice Bench"})).raise_for_status().json()
        user_id, project_id = user["id"], None
        print(f"voice bench user {email}", flush=True)
        try:
            project_id = (await http.post("/projects", json={"name": "Voice bench"})).json()["id"]
            with SAMPLE_DOC.open("rb") as f:
                (await http.post("/documents", params={"project_id": project_id},
                                 files={"file": (SAMPLE_DOC.name, f, "text/markdown")}, timeout=600)).raise_for_status()
            chat_id = (await http.post(f"/projects/{project_id}/chats", json={})).json()["id"]

            print("synthesising the questions…", flush=True)
            audio = {text: await synthesise(runtime, text) for _, text in SCRIPT}
            audio[BARGE_IN] = await synthesise(runtime, BARGE_IN)
            barge_lead = leading_silence(audio[BARGE_IN])

            cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
            url = args.core.replace("http", "ws", 1) + f"/ws/voice/{chat_id}"
            async with websockets.connect(url, additional_headers={"Origin": ORIGIN, "Cookie": cookie},
                                          max_size=None) as ws:
                browser = Browser(ws)
                speakers = asyncio.create_task(browser.speakers())
                t0 = time.perf_counter()
                await browser.until("ready", timeout=600)
                print(f"session ready in {time.perf_counter() - t0:.1f}s", flush=True)
                mic = asyncio.create_task(browser.microphone())
                sampler.start()
                started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                await asyncio.sleep(1.0)

                turn = 0
                for n in range(args.exchanges):
                    label, text = SCRIPT[n % len(SCRIPT)]
                    browser.say(audio[text])
                    turn += 1
                    _, tr = await browser.until("transcript", turn=turn)
                    spoke_end = browser.spoke_last_at
                    first_at, _ = await browser.until("audio", turn=turn)
                    row = {"n": n + 1, "label": label, "said": text, "heard": tr["text"],
                           "wer": round(word_error_rate(text, tr["text"]), 3),
                           "first_audio_ms": round((first_at - spoke_end) * 1000)}

                    if args.barge_every and (n + 1) % args.barge_every == 0:
                        await asyncio.sleep(0.6)
                        browser.say(audio[BARGE_IN])
                        # When the speech itself starts, past the clip's silent lead-in.
                        onset = time.perf_counter() + barge_lead
                        paused_at, _ = await browser.until("pause", turn=None)
                        stopped_at, _ = await browser.until("interrupted", turn=turn)
                        _, done = await browser.until("done", turn=turn)
                        barges.append({"n": n + 1, "pause_ms": round((paused_at - onset) * 1000),
                                       "stop_ms": round((stopped_at - onset) * 1000),
                                       "stored": done["assistant_message"]["content"]})
                        row["interrupted"] = True
                        turn += 1  # what was said over it is the next turn
                        _, tr2 = await browser.until("transcript", turn=turn)
                        _, done = await browser.until("done", turn=turn, timeout=120)
                        row["barge_heard"] = tr2["text"]
                    else:
                        _, done = await browser.until("done", turn=turn, timeout=120)
                    meta = done["assistant_message"].get("metadata", {})
                    row["server"] = meta.get("voice", {})
                    row["timings"] = meta.get("timings", {})
                    row["route"] = meta.get("route")
                    row["answer"] = done["assistant_message"]["content"][:160]
                    results.append(row)
                    print(f"  {row['n']:2d} {label:10s} first audio {row['first_audio_ms']/1000:5.2f}s  "
                          f"wer {row['wer']:.2f}  heard {tr['text']!r}", flush=True)
                    await asyncio.sleep(0.8)

                mic.cancel()
                speakers.cancel()
        finally:
            await sampler.stop()
            print("cleaning up…", flush=True)
            await _cleanup(http, db, user_id, project_id)
            await db.close()

    firsts = [r["first_audio_ms"] for r in results]
    # The server's measure starts at the last frame the VAD heard as speech, so
    # it excludes the trailing silence in the synthesised question: closer to
    # what a person experiences, and the one the target is judged on.
    server = [r["server"]["first_audio_ms"] for r in results if r["server"].get("first_audio_ms")]
    report = {
        "started_at": started_at,
        "exchanges": len(results),
        "first_audio_ms": {"p50": _pct(firsts, 0.5), "p95": _pct(firsts, 0.95), "max": max(firsts),
                           "mean": round(statistics.fmean(firsts))},
        "server_first_audio_ms": {"p50": _pct(server, 0.5), "p95": _pct(server, 0.95), "max": max(server),
                                  "mean": round(statistics.fmean(server))} if server else None,
        "wer_mean": round(statistics.fmean(r["wer"] for r in results), 3),
        "barge_in": barges,
        "results": results,
        "memory": {"peaks": sampler.peaks, "min_available": sampler.min_available},
    }
    print(json.dumps({k: report[k] for k in ("server_first_audio_ms", "first_audio_ms", "wer_mean")}, indent=2))
    if args.out:
        out = Path(args.out)
        out.with_suffix(".json").write_text(json.dumps(report, indent=2))
        out.with_suffix(".md").write_text(_markdown(report))
        print(f"wrote {out}.json and {out}.md")
    return 0


def _markdown(r: dict) -> str:
    f = r["first_audio_ms"]
    sv = r["server_first_audio_ms"] or {}
    lines = ["# Voice benchmark", "", f"{r['started_at']} · {r['exchanges']} exchanges", "",
             "End of speech to the first answer audio, both including the end-of-turn silence window. "
             "*Server*: from the last frame voice detection heard as speech to the audio leaving core "
             "(the target's measure). *Client*: from the end of the synthesised question clip, which "
             "carries some trailing silence, to the audio arriving.", "",
             "| Measure | p50 | p95 | max | mean |", "|---|---|---|---|---|",
             f"| Server | {sv.get('p50', 0)/1000:.2f}s | {sv.get('p95', 0)/1000:.2f}s | "
             f"{sv.get('max', 0)/1000:.2f}s | {sv.get('mean', 0)/1000:.2f}s |",
             f"| Client | {f['p50']/1000:.2f}s | {f['p95']/1000:.2f}s | {f['max']/1000:.2f}s | {f['mean']/1000:.2f}s |", "",
             f"Mean word error rate of the transcripts: {r['wer_mean']:.1%}.", ""]
    if r["barge_in"]:
        lines += ["## Barge-in", "", "From the onset of speech over the answer. *Paused* includes the "
                  "300 ms of speech core waits for before reacting, so the reaction itself is paused "
                  "minus 300 ms. *Stopped* adds transcribing the overlap to rule out \"mm-hmm\".", "", "| Exchange | Paused | Stopped | Stored as heard |",
                  "|---|---|---|---|"]
        for b in r["barge_in"]:
            lines.append(f"| {b['n']} | {b['pause_ms']} ms | {b['stop_ms']} ms | {b['stored'][:80]!r} |")
        lines.append("")
    lines += ["## Every exchange", "", "| # | Kind | First audio (server) | STT | First token | TTS first | WER | Heard |",
              "|---|---|---|---|---|---|---|---|"]
    for x in r["results"]:
        s = x["server"]
        lines.append(f"| {x['n']} | {x['label']}{' (interrupted)' if x.get('interrupted') else ''} | "
                     f"{s.get('first_audio_ms', 0)/1000:.2f}s | {s.get('stt_ms', '–')} ms | "
                     f"{x.get('timings', {}).get('first_token_ms', '–')} ms | {s.get('tts_first_ms', '–')} ms | "
                     f"{x['wer']:.2f} | {x['heard']} |")
    lines += ["", "## Peak memory", "", "| Process | Peak |", "|---|---|"]
    for name, size in sorted(r["memory"]["peaks"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {_gb(size)} |")
    if r["memory"]["min_available"]:
        lines += ["", f"Lowest available system memory: {_gb(r['memory']['min_available'])}.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
