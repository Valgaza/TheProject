"""Benchmark a running Nexus stack: where a turn spends its time, and how much
memory everything holds while it does.

Drives core's public API as a throwaway user, the way the frontend would:
signs up, makes a project, uploads a document, then replays a fixed script of
turns (small talk, a fact worth remembering, document questions, a recall in a
new chat, a web search, a general question). Every answer carries
``meta.timings`` (see core/nexus_core/chat_service.py); this collects them,
waits for the memory writes the turns caused, samples the memory footprint of
every service while it runs, and deletes everything it created at the end.

    uv run python scripts/bench.py --out docs/benchmarks/2026-10-baseline
    uv run python scripts/bench.py --cold        # unload the model first

Needs the stack up (./start.sh) and the repo-root .env (for DATABASE_URL,
which is how it reads write timings and removes its own account).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
import psutil
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOC = ROOT / "rag" / "sample_docs" / "sample.md"

# (label, where, content, use_web). "project" turns run in one chat inside the
# bench project; "recall" opens a second chat in the same project after the
# memory writes have landed; "loose" runs outside any project.
WARM_SCRIPT: list[tuple[str, str, str, bool]] = [
    ("small-talk", "project", "Thanks, that's helpful.", False),
    ("remember", "project", "For this project I'm using Qdrant as the vector store.", False),
    ("doc-1", "project", "According to my documents, what is hybrid search?", False),
    ("doc-2", "project", "What do my documents say about chunking strategies?", False),
    ("general", "loose", "Explain the difference between TCP and UDP in two sentences.", False),
    ("web", "loose", "What's new with the Raft consensus algorithm?", True),
]
RECALL_TURN = ("recall", "recall", "Which vector store am I using for this project?", False)
FIRST_TURN = ("first", "project", "Hi! Quick check that you're there.", False)

# Which processes count as which service, matched against the command line.
# "runtime" holds every model under MLX; "ollama" only appears on the fallback.
SERVICES = {
    "core": "nexus_core.main",
    "memory": "nexus_memory.main",
    "rag": "rag_api",
    "runtime": "nexus_runtime",
    "ollama": "ollama",
}


# ── Memory sampling ──────────────────────────────────────────────────────────


def _service_pids() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {name: [] for name in SERVICES}
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or []) or (proc.info["name"] or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for name, needle in SERVICES.items():
            if needle in cmd and "bench.py" not in cmd:
                found[name].append(proc.info["pid"])
    return found


_FOOTPRINT = re.compile(r"\[(\d+)\].*?Footprint:\s*([\d.]+)\s*(B|KB|MB|GB)")
_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def _footprints(pids: list[int]) -> dict[int, int]:
    """Bytes per pid as macOS accounts it (what Activity Monitor shows),
    which unlike RSS includes the GPU allocations a model server holds."""
    if not pids:
        return {}
    out = subprocess.run(
        ["/usr/bin/footprint", *[arg for pid in pids for arg in ("-p", str(pid))]],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return {int(pid): int(float(n) * _UNIT[unit]) for pid, n, unit in _FOOTPRINT.findall(out)}


def _docker_mem() -> dict[str, int]:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    sizes: dict[str, int] = {}
    for line in out.splitlines():
        name, _, usage = line.partition("\t")
        m = re.match(r"([\d.]+)\s*([KMG]i?B)", usage)
        if m:
            unit = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "KB": 1000, "MB": 1000**2, "GB": 1000**3}
            sizes[name] = int(float(m.group(1)) * unit.get(m.group(2), 1))
    return sizes


@dataclass
class MemorySampler:
    interval: float = 3.0
    peaks: dict[str, int] = field(default_factory=dict)
    min_available: int | None = None
    _task: asyncio.Task | None = None

    def _sample(self) -> None:
        groups = _service_pids()
        sizes = _footprints([pid for pids in groups.values() for pid in pids])
        for name, pids in groups.items():
            total = sum(sizes.get(pid, 0) for pid in pids)
            if total:
                self.peaks[name] = max(self.peaks.get(name, 0), total)
        for name, size in _docker_mem().items():
            key = f"docker:{name}"
            self.peaks[key] = max(self.peaks.get(key, 0), size)
        available = psutil.virtual_memory().available
        self.min_available = available if self.min_available is None else min(self.min_available, available)

    async def _run(self) -> None:
        while True:
            await asyncio.to_thread(self._sample)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(self._sample)


# ── Driving core ─────────────────────────────────────────────────────────────


@dataclass
class TurnResult:
    label: str
    wall_ms: int
    route: str
    timings: dict[str, int]
    error: str | None


async def _turn(http: httpx.AsyncClient, chat_id: str, label: str, content: str, use_web: bool) -> TurnResult:
    """One turn through the streaming endpoint, as the UI sends it, so the
    first visible token is measured where a person would see it."""
    started = time.perf_counter()
    first_token_ms = None
    body = None
    async with http.stream(
        "POST", f"/chats/{chat_id}/messages/stream",
        json={"content": content, "use_web": use_web}, timeout=600,
    ) as response:
        response.raise_for_status()
        event = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                if event == "token" and first_token_ms is None:
                    first_token_ms = round((time.perf_counter() - started) * 1000)
                elif event == "done":
                    body = json.loads(line[6:])
                elif event == "error":
                    raise RuntimeError(line[6:])
    wall_ms = round((time.perf_counter() - started) * 1000)
    meta = body["assistant_message"]["metadata"]
    timings = dict(meta.get("timings", {}))
    if first_token_ms is not None:
        timings["client_first_token_ms"] = first_token_ms
    result = TurnResult(label, wall_ms, body["route"]["handler"], timings, meta.get("error"))
    print(f"  {label:<11} {wall_ms/1000:6.1f}s  route={result.route:<8} "
          + " ".join(f"{k.removesuffix('_ms')}={v}" for k, v in result.timings.items()), flush=True)
    return result


async def _cold_reload(base_url: str, token: str) -> int | None:
    """Measure what a cold model costs. Returns the reload time in ms.

    Against the MLX runtime the model is unloaded and loaded again through
    its residency endpoints; the runtime never loads lazily, so this is the
    cost start.sh pays, not one a turn ever pays. Against Ollama (the
    fallback engine) the model is stopped and the first turn pays the load.
    """
    if ":11434" in base_url:
        listed = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.splitlines()[1:]
        for line in listed:
            subprocess.run(["ollama", "stop", line.split()[0]], check=False)
            print(f"  unloaded {line.split()[0]} (Ollama)", flush=True)
        return None
    root = base_url.rstrip("/").removesuffix("/v1")
    async with httpx.AsyncClient(base_url=root, headers={"X-Service-Token": token}, timeout=900) as rt:
        (await rt.post("/models/chat/unload")).raise_for_status()
        t0 = time.perf_counter()
        (await rt.post("/models/chat/load")).raise_for_status()
        ms = round((time.perf_counter() - t0) * 1000)
    print(f"  chat model reloaded in {ms/1000:.1f}s (runtime)", flush=True)
    return ms


async def _wait_for_writes(db: asyncpg.Connection, user_id: str, timeout: float) -> list[dict]:
    """Wait until no memory write for this user is pending, then return them.
    The gate runs after the reply, so a row can appear a few seconds late;
    quiet for 20s with nothing pending counts as settled."""
    deadline = time.monotonic() + timeout
    quiet_since: float | None = None
    last_count = -1
    while time.monotonic() < deadline:
        rows = await db.fetch(
            """
            select e.status, e.scope, e.created_at, e.updated_at
            from memory_episodes e join chats c on c.id = e.chat_id
            where c.user_id = $1
            """,
            uuid.UUID(user_id),
        )
        pending = sum(1 for r in rows if r["status"] == "pending")
        if pending == 0 and len(rows) == last_count:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 20:
                break
        else:
            quiet_since = None
        last_count = len(rows)
        await asyncio.sleep(2)
    return [
        {
            "status": r["status"],
            "scope": r["scope"],
            "ingest_ms": round((r["updated_at"] - r["created_at"]).total_seconds() * 1000),
        }
        for r in rows
    ]


async def _cleanup(http: httpx.AsyncClient, db: asyncpg.Connection, user_id: str, project_id: str | None) -> None:
    for doc in (await http.get("/documents")).json():
        await http.delete(f"/documents/{doc['id']}")
    if project_id:
        await http.delete("/graph", params={"view": "project", "project_id": project_id})
    await http.delete("/graph", params={"view": "user"})
    for chat in (await http.get("/chats")).json():
        await http.delete(f"/chats/{chat['id']}")
    if project_id:
        await http.delete(f"/projects/{project_id}")
    await db.execute("delete from profiles where id = $1", uuid.UUID(user_id))


# ── Reporting ────────────────────────────────────────────────────────────────


def _pct(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def _stage_table(turns: list[TurnResult]) -> list[dict]:
    stages: dict[str, list[int]] = {}
    for t in turns:
        stages.setdefault("wall_ms", []).append(t.wall_ms)
        for k, v in t.timings.items():
            stages.setdefault(k, []).append(v)
    return [
        {"stage": k, "n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "max": max(v),
         "mean": round(statistics.fmean(v))}
        for k, v in stages.items()
    ]


def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB"


def _markdown(report: dict) -> str:
    env, lines = report["environment"], []
    lines += [f"# Benchmark: {report['label']}", "",
              f"{report['started_at']} · `{env['git']}` · {env['machine']} · "
              f"chat model `{env['chat_model']}` on {env['engine']} · cold start: {report['cold']}", "",
              "## Per stage (warm turns)", "",
              "| Stage | n | p50 | p95 | max |", "|---|---|---|---|---|"]
    for row in report["stages_warm"]:
        lines.append(f"| {row['stage']} | {row['n']} | {row['p50']/1000:.2f}s | "
                     f"{row['p95']/1000:.2f}s | {row['max']/1000:.2f}s |")
    first = report["first_turn"]
    if report.get("model_reload_ms") is not None:
        lines += ["", f"**Chat model reload** (unload, then load): {report['model_reload_ms']/1000:.1f}s"]
    lines += ["", f"**First turn** ({'after a cold reload' if report['cold'] else 'as found'}): "
              f"{first['wall_ms']/1000:.1f}s wall, timings {first['timings']}", "",
              "## Every turn", "", "| Turn | Wall | Route | Timings |", "|---|---|---|---|"]
    for t in report["turns"]:
        timings = ", ".join(f"{k.removesuffix('_ms')} {v/1000:.2f}s" for k, v in t["timings"].items())
        lines.append(f"| {t['label']} | {t['wall_ms']/1000:.1f}s | {t['route']} | {timings} |")
    writes = report["memory_writes"]
    lines += ["", "## Memory writes", ""]
    if writes:
        ms = [w["ingest_ms"] for w in writes]
        lines.append(f"{len(writes)} episodes; ingest p50 {_pct(ms, 0.5)/1000:.1f}s, "
                     f"max {max(ms)/1000:.1f}s; statuses {sorted({w['status'] for w in writes})}")
    else:
        lines.append("None: the gate kept nothing from this run.")
    lines += ["", "## Peak memory", "", "| Process | Peak |", "|---|---|"]
    for name, size in sorted(report["memory"]["peaks"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {_gb(size)} |")
    lines += ["", f"Lowest available system memory during the run: "
              f"{_gb(report['memory']['min_available'])} of {_gb(env['total_memory'])}.", ""]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--core", default="http://localhost:8100")
    parser.add_argument("--rounds", type=int, default=2, help="times to replay the warm script")
    parser.add_argument("--cold", action="store_true", help="unload the model before the first turn")
    parser.add_argument("--label", default="bench")
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    parser.add_argument("--write-timeout", type=float, default=600, help="seconds to wait for memory writes")
    args = parser.parse_args()

    env_file = dotenv_values(ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL") or env_file.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not found in the environment or .env", file=sys.stderr)
        return 2

    sampler = MemorySampler()
    db = await asyncpg.connect(database_url)
    async with httpx.AsyncClient(base_url=args.core, timeout=60) as http:
        health = (await http.get("/health")).json()
        if health.get("status") != "ok":
            print(f"stack is not healthy: {health}", file=sys.stderr)
            return 1

        email = f"bench-{uuid.uuid4().hex[:12]}@example.com"
        user = (await http.post("/auth/signup", json={
            "email": email, "password": uuid.uuid4().hex, "display_name": "Bench User",
        })).raise_for_status().json()
        user_id, project_id = user["id"], None
        print(f"bench user {email}", flush=True)

        sampler.start()
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            project_id = (await http.post("/projects", json={"name": "Bench"})).raise_for_status().json()["id"]
            with SAMPLE_DOC.open("rb") as f:
                t0 = time.perf_counter()
                doc = (await http.post(
                    "/documents", params={"project_id": project_id},
                    files={"file": (SAMPLE_DOC.name, f, "text/markdown")}, timeout=600,
                )).raise_for_status().json()
                upload_ms = round((time.perf_counter() - t0) * 1000)
            print(f"  upload      {upload_ms/1000:6.1f}s  status={doc['status']} chunks={doc['chunk_count']}", flush=True)

            project_chat = (await http.post(f"/projects/{project_id}/chats", json={})).json()["id"]
            loose_chat = (await http.post("/chats", json={})).json()["id"]
            chats = {"project": project_chat, "loose": loose_chat}

            reload_ms = None
            if args.cold:
                reload_ms = await _cold_reload(
                    env_file.get("OPENAI_BASE_URL") or "", env_file.get("SERVICE_TOKEN") or ""
                )
            label, where, content, use_web = FIRST_TURN
            first = await _turn(http, chats[where], label, content, use_web)

            warm: list[TurnResult] = []
            for round_no in range(args.rounds):
                print(f"round {round_no + 1}/{args.rounds}", flush=True)
                for label, where, content, use_web in WARM_SCRIPT:
                    warm.append(await _turn(http, chats[where], label, content, use_web))

            print("waiting for memory writes to settle…", flush=True)
            writes = await _wait_for_writes(db, user_id, args.write_timeout)
            recall_chat = (await http.post(f"/projects/{project_id}/chats", json={})).json()["id"]
            label, _, content, use_web = RECALL_TURN
            warm.append(await _turn(http, recall_chat, label, content, use_web))
        finally:
            await sampler.stop()
            print("cleaning up…", flush=True)
            await _cleanup(http, db, user_id, project_id)
            await db.close()

    report = {
        "label": args.label,
        "started_at": started_at,
        "cold": args.cold,
        "environment": {
            "git": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.strip(),
            "machine": f"{platform.machine()} {platform.mac_ver()[0]}",
            "chip": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                   capture_output=True, text=True).stdout.strip(),
            "total_memory": psutil.virtual_memory().total,
            "chat_model": health.get("chat_model"),
            "engine": "ollama" if ":11434" in (env_file.get("OPENAI_BASE_URL") or "") else "mlx runtime",
        },
        "upload_ms": upload_ms,
        "model_reload_ms": reload_ms,
        "first_turn": first.__dict__,
        "turns": [t.__dict__ for t in warm],
        "stages_warm": _stage_table(warm),
        "memory_writes": writes,
        "memory": {"peaks": sampler.peaks, "min_available": sampler.min_available},
    }
    markdown = _markdown(report)
    print("\n" + markdown)
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        (out.parent / f"{out.name}.json").write_text(json.dumps(report, indent=2, default=str))
        (out.parent / f"{out.name}.md").write_text(markdown)
        print(f"wrote {(out.parent / f"{out.name}.json")} and {(out.parent / f"{out.name}.md")}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
