"""The models themselves: loading them, generating, embedding, unloading.

Everything here that touches MLX runs on the scheduler's worker thread
(engine.py). The asyncio side only builds jobs and reads their events.

Three things this does that a stock ``mlx_lm.server`` does not, and that are
the reason this service exists:

- **Priorities.** A generation is a job stepped one token at a time, so a live
  reply overtakes background extraction mid-flight (engine.py).
- **Schema-constrained JSON.** ``response_format: json_schema`` compiles the
  schema with xgrammar into a token mask applied before every sample, so the
  output cannot be anything but valid JSON for that schema. Graphiti's
  extraction depends on this; Ollama provided it, mlx-lm alone does not.
- **Prompt reuse.** Each finished generation leaves its KV cache in an LRU
  keyed by tokens, and the next request with a shared prefix (the same
  conversation, one turn later) only processes the tokens that are new.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .config import Settings
from .engine import Priority, Scheduler

logger = logging.getLogger(__name__)


# ── Requests and events ──────────────────────────────────────────────────────


class ModelNotLoaded(RuntimeError):
    """A request named a model that is unloaded right now (an unload in
    progress, a voice model outside a session). Answered as 503, not 500."""


@dataclass
class GenParams:
    max_tokens: int
    temperature: float = 0.0
    top_p: float = 1.0
    stop: list[str] = field(default_factory=list)
    json_schema: dict | None = None
    enable_thinking: bool = False


@dataclass
class Event:
    kind: str  # "text" | "done" | "error"
    text: str = ""
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


class ThinkFilter:
    """Drops ``<think>…</think>`` spans from streamed text.

    With thinking off the chat template already closes the think block, so
    this normally passes everything through. It is here for requests that ask
    for reasoning, and for models that think regardless: the reasoning is not
    the answer and must never reach a person or a JSON parser.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        out = []
        while self.pending:
            tag = self.CLOSE if self.inside else self.OPEN
            at = self.pending.find(tag)
            if at >= 0:
                if not self.inside:
                    out.append(self.pending[:at])
                self.pending = self.pending[at + len(tag):]
                self.inside = not self.inside
                continue
            # Hold back anything that could be the start of a tag.
            keep = next(
                (k for k in range(min(len(tag) - 1, len(self.pending)), 0, -1)
                 if tag.startswith(self.pending[-k:])),
                0,
            )
            if not self.inside:
                out.append(self.pending[: len(self.pending) - keep])
            self.pending = self.pending[len(self.pending) - keep:]
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self.pending = ("" if self.inside else self.pending), ""
        return rest


class StopFilter:
    """Ends the text at the first stop string, holding back possible prefixes."""

    def __init__(self, stops: list[str]) -> None:
        self.stops = [s for s in stops if s]
        self.held = ""
        self.hit = False

    def feed(self, text: str) -> str:
        if not self.stops or self.hit:
            return "" if self.hit else text
        self.held += text
        for s in self.stops:
            at = self.held.find(s)
            if at >= 0:
                self.hit = True
                out, self.held = self.held[:at], ""
                return out
        keep = max(len(s) for s in self.stops) - 1
        out, self.held = self.held[: max(0, len(self.held) - keep)], self.held[max(0, len(self.held) - keep):]
        return out

    def flush(self) -> str:
        out, self.held = ("" if self.hit else self.held), ""
        return out


# ── Constrained decoding ─────────────────────────────────────────────────────


class JsonSchemaProcessor:
    """An mlx-lm logits processor that only allows tokens the schema allows.

    mlx-lm calls it before every sample with the tokens so far. The first call
    carries the prompt, which the grammar must not see; every later call ends
    with the token just sampled, which is fed to the matcher before the next
    mask is computed. Once the grammar completes, only the stop token remains
    allowed, so generation ends exactly where the JSON does.
    """

    def __init__(self, backend: "MLXBackend", schema: dict) -> None:
        self.backend = backend
        self.schema = schema
        self.matcher = None
        self.bitmask = None
        self.vocab_size = 0

    def __call__(self, tokens, logits):
        import mlx.core as mx
        import xgrammar as xgr
        from xgrammar.kernels.apply_token_bitmask_mlx import apply_token_bitmask_mlx

        if self.matcher is None:
            self.vocab_size = int(logits.shape[-1])
            compiled = self.backend.compiled_schema(self.schema, self.vocab_size)
            self.matcher = xgr.GrammarMatcher(compiled)
            self.bitmask = xgr.allocate_token_bitmask(1, self.vocab_size)
        else:
            if not self.matcher.is_terminated():
                self.matcher.accept_token(int(tokens[-1].item()))
        if self.matcher.is_terminated():
            return logits
        self.matcher.fill_next_token_bitmask(self.bitmask)
        return apply_token_bitmask_mlx(mx.array(self.bitmask.numpy()), logits, self.vocab_size)


# ── A generation, one token per step ─────────────────────────────────────────


class GenerationJob:
    def __init__(
        self,
        backend: "MLXBackend",
        prompt_tokens: list[int],
        params: GenParams,
        priority: Priority,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
    ) -> None:
        self.backend = backend
        self.prompt_tokens = prompt_tokens
        self.params = params
        self.priority = priority
        self.loop = loop
        self.queue = queue
        self.cancelled = False
        self.generated: list[int] = []
        self.cached_tokens = 0
        self.submitted = time.perf_counter()
        self.started: float | None = None
        self._gen = None
        self._cache = None
        self._detok = None
        self._think = ThinkFilter()
        self._stop = StopFilter(params.stop)

    def _emit(self, event: Event) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def _start(self) -> None:
        self.started = time.perf_counter()
        import mlx.core as mx
        from mlx_lm.generate import generate_step
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
        from mlx_lm.sample_utils import make_sampler

        slot = self.backend.chat_slot()
        model, tokenizer = slot.obj
        tokens = self.prompt_tokens
        cache, rest = slot.prompt_cache.fetch_nearest_cache(slot.repo, tokens)
        if cache is None:
            cache, rest = make_prompt_cache(model), tokens
        elif not rest:
            # An exact hit still needs one token to run the model on.
            trim_prompt_cache(cache, 1)
            rest = tokens[-1:]
        self.cached_tokens = len(tokens) - len(rest)
        self._cache = cache

        self._detok = tokenizer.detokenizer
        self._detok.reset()
        processors = [JsonSchemaProcessor(self.backend, self.params.json_schema)] if self.params.json_schema else None
        self._gen = generate_step(
            mx.array(rest),
            model,
            max_tokens=self.params.max_tokens,
            sampler=make_sampler(temp=self.params.temperature, top_p=self.params.top_p),
            logits_processors=processors,
            prompt_cache=cache,
            prefill_step_size=self.backend.settings.prefill_step,
        )
        self._eos = set(tokenizer.eos_token_ids)

    def _text(self, segment: str) -> None:
        text = self._stop.feed(self._think.feed(segment))
        if text:
            self._emit(Event("text", text))

    def _finish(self, reason: str) -> bool:
        self._detok.finalize()
        self._text(self._detok.last_segment)
        tail = self._stop.feed(self._think.flush()) + self._stop.flush()
        if tail:
            self._emit(Event("text", tail))
        self.close()
        self.backend.remember_prefix(self.prompt_tokens + self.generated, self._cache)
        now = time.perf_counter()
        running = now - (self.started or now)
        # One line per generation: where a slow call spent its time (waiting
        # its turn, or generating) and whether the prompt cache helped.
        logger.info(
            "generation priority=%s prompt=%d cached=%d out=%d finish=%s waited=%.2fs ran=%.2fs %.1f tok/s",
            self.priority.name.lower(), len(self.prompt_tokens), self.cached_tokens,
            len(self.generated), reason, (self.started or now) - self.submitted, running,
            len(self.generated) / running if running > 0 else 0.0,
        )
        self._emit(Event(
            "done",
            finish_reason=reason,
            prompt_tokens=len(self.prompt_tokens),
            completion_tokens=len(self.generated),
            cached_tokens=self.cached_tokens,
        ))
        return True

    def step(self) -> bool:
        if self._gen is None:
            self._start()
        try:
            token, _ = next(self._gen)
        except StopIteration:
            return self._finish("length")
        token = int(token.item() if hasattr(token, "item") else token)
        if token in self._eos:
            return self._finish("stop")
        self.generated.append(token)
        self._detok.add_token(token)
        self._text(self._detok.last_segment)
        if self._stop.hit:
            return self._finish("stop")
        if len(self.generated) >= self.params.max_tokens:
            return self._finish("length")
        return False

    def close(self) -> None:
        """Close the generator here, on the worker thread that made it.

        mlx-lm's generate_step holds an MLX stream context, which must be torn
        down on the thread that opened it. Left to the garbage collector it
        is finalised on whichever thread happens to run, and MLX complains.
        """
        if self._gen is not None:
            self._gen.close()
            self._gen = None

    def fail(self, exc: BaseException) -> None:
        self.close()
        self._emit(Event("error", text=f"{exc.__class__.__name__}: {exc}"))


# ── Residency ────────────────────────────────────────────────────────────────


@dataclass
class ModelSlot:
    name: str          # "chat" or "embed"
    repo: str          # the Hugging Face id, and the name it is served under
    lifetime: str      # "always" | "session"
    obj: Any = None
    nbytes: int = 0
    prompt_cache: Any = None
    loaded_at: float | None = None

    @property
    def loaded(self) -> bool:
        return self.obj is not None


def _param_bytes(model) -> int:
    from mlx.utils import tree_flatten

    return sum(v.nbytes for _, v in tree_flatten(model.parameters()))


class MLXBackend:
    def __init__(self, settings: Settings, scheduler: Scheduler) -> None:
        self.settings = settings
        self.scheduler = scheduler
        self.slots = {
            "chat": ModelSlot("chat", settings.chat_model, "always"),
            "embed": ModelSlot("embed", settings.embed_model, "always"),
        }
        if settings.decide_enabled:
            self.slots["decide"] = ModelSlot("decide", settings.decide_model, "optional")
        # Voice: loaded per session (or on first use) and unloaded when idle.
        self.slots["stt"] = ModelSlot("stt", settings.stt_model, "session")
        self.slots["tts"] = ModelSlot("tts", settings.tts_model, "session")
        self.last_used: dict[str, float] = {}
        self.pronunciation: str | None = None
        self._compilers: dict[int, Any] = {}
        self._grammars: dict[tuple[int, str], Any] = {}
        self.ready = False

    # ── naming ───────────────────────────────────────────────────────────────

    def resolve(self, kind: str, requested: str | None) -> ModelSlot | None:
        """The slot a request names. Accepts the full repo id or its last
        part, so "Qwen3-8B-4bit" and "mlx-community/Qwen3-8B-4bit" agree."""
        slot = self.slots[kind]
        if not requested:
            return slot
        want = requested.lower()
        if want in (slot.repo.lower(), slot.repo.rsplit("/", 1)[-1].lower(), kind):
            return slot
        return None

    def chat_slot(self) -> ModelSlot:
        slot = self.slots["chat"]
        if not slot.loaded:
            raise ModelNotLoaded(f"chat model {slot.repo} is not loaded")
        return slot

    # ── loading (worker thread) ──────────────────────────────────────────────

    def _load(self, name: str) -> dict:
        import mlx.core as mx

        slot = self.slots[name]
        if slot.loaded:
            return self._describe(slot)
        started = time.perf_counter()
        if name == "chat":
            from mlx_lm import load
            from mlx_lm.models.cache import LRUPromptCache

            model, tokenizer = load(slot.repo)
            slot.obj = (model, tokenizer)
            slot.nbytes = _param_bytes(model)
            slot.prompt_cache = LRUPromptCache(
                max_size=self.settings.prompt_cache_entries,
                max_bytes=int(self.settings.prompt_cache_gb * 1024**3),
            )
        elif name == "stt":
            from parakeet_mlx import from_pretrained

            before = mx.get_active_memory()
            slot.obj = from_pretrained(slot.repo)
            slot.nbytes = max(0, mx.get_active_memory() - before)
        elif name == "tts":
            from . import pronunciation

            if self.pronunciation is None:
                # Before mlx-audio imports misaki: see pronunciation.py.
                self.pronunciation = pronunciation.install()
            from mlx_audio.tts.utils import load_model

            before = mx.get_active_memory()
            model = load_model(slot.repo)
            # Voices live in the same repo as the weights; without this,
            # Kokoro looks for them in a different one.
            model.repo_id = slot.repo
            # The per-language pipeline (and its spaCy model) is built on first
            # use; build it now so the first sentence spoken is not the slow one.
            for _ in model.generate(text="Ready.", voice=self.settings.tts_voice, lang_code="a"):
                pass
            slot.obj = model
            slot.nbytes = max(0, mx.get_active_memory() - before)
        elif name == "embed":
            from mlx_embeddings.utils import load

            model, tokenizer = load(slot.repo)
            slot.obj = (model, tokenizer)
            slot.nbytes = _param_bytes(model)
        else:
            from huggingface_hub import snapshot_download
            from laya_mlx import Agent

            import os

            sub = self.settings.decide_subfolder or None
            if os.path.isdir(slot.repo):
                # A local checkpoint, e.g. one fine-tuned by scripts/laya/finetune.py.
                local = slot.repo
            else:
                try:
                    # Only from disk: an optional model must never hold startup
                    # hostage to a slow download. Fetch it ahead of time, then
                    # POST /models/decide/load.
                    local = snapshot_download(
                        slot.repo, allow_patterns=[f"{sub}/*"] if sub else None, local_files_only=True
                    )
                except Exception as exc:
                    raise ModelNotLoaded(
                        f"{slot.repo} ({sub or 'root'}) is not downloaded yet"
                    ) from exc
            before = mx.get_active_memory()
            agent = Agent(local, subfolder=sub, dtype="float16")
            slot.obj = agent
            # Laya keeps its weights inside its own wrapper; what it added to
            # MLX's allocation is the honest size.
            slot.nbytes = max(0, mx.get_active_memory() - before)
        slot.loaded_at = time.time()
        logger.info(
            "loaded %s (%s) in %.1fs, %.2f GB weights, %.2f GB active",
            name, slot.repo, time.perf_counter() - started,
            slot.nbytes / 1024**3, mx.get_active_memory() / 1024**3,
        )
        return self._describe(slot)

    def _unload(self, name: str) -> dict:
        import mlx.core as mx

        slot = self.slots[name]
        slot.obj = None
        slot.prompt_cache = None
        slot.nbytes = 0
        slot.loaded_at = None
        if name == "chat":
            self._compilers.clear()
            self._grammars.clear()
        gc.collect()
        # MLX keeps freed buffers for reuse; without this an unload frees
        # nothing as far as the rest of the machine can tell.
        mx.clear_cache()
        logger.info("unloaded %s, %.2f GB active", name, mx.get_active_memory() / 1024**3)
        return self._describe(slot)

    def _warm(self) -> None:
        """One tiny generation and one embedding, so the first real request
        pays for nothing (kernel compilation, first allocations)."""
        model, tokenizer = self.chat_slot().obj
        from mlx_lm.generate import generate_step
        import mlx.core as mx

        prompt = tokenizer.apply_chat_template(
            [{"role": "user", "content": "Hi"}], add_generation_prompt=True,
        )
        for _ in zip(range(2), generate_step(mx.array(prompt), model, max_tokens=2)):
            pass
        self._embed_now(["warm up"], None)
        decide = self.slots.get("decide")
        if decide is not None and decide.loaded:
            decide.obj.predict({"text": "warm up"}, {"q": {"type": "noul", "instructions": "Is `text` a test?"}})

    async def load(self, name: str) -> dict:
        self.last_used[name] = time.monotonic()
        return await self.scheduler.call(lambda: self._load(name), Priority.LIVE)

    async def unload(self, name: str) -> dict:
        return await self.scheduler.call(lambda: self._unload(name), Priority.LIVE)

    async def start(self) -> None:
        """Load and warm every always-resident model, then report ready."""
        import mlx.core as mx

        if self.settings.memory_limit_gb:
            mx.set_memory_limit(int(self.settings.memory_limit_gb * 1024**3))
        mx.set_cache_limit(int(self.settings.mlx_cache_limit_gb * 1024**3))
        for name, slot in self.slots.items():
            if slot.lifetime == "always":
                await self.load(name)
            elif slot.lifetime == "optional":
                # Wanted, not required: without it /decide answers 503 and
                # core keeps its old logic.
                try:
                    await self.load(name)
                except Exception:
                    logger.exception("optional model %s (%s) did not load", name, slot.repo)
        await self.scheduler.call(self._warm, Priority.LIVE)
        self.ready = True
        logger.info("runtime ready")

    # ── generation ───────────────────────────────────────────────────────────

    def compiled_schema(self, schema: dict, vocab_size: int):
        import xgrammar as xgr

        # Sorted only for the cache key. The grammar is compiled from the
        # schema as written, because xgrammar emits properties in schema
        # order and that order is part of the prompt design: a model that
        # writes "persist" before "reason" is deciding before explaining.
        key = (vocab_size, json.dumps(schema, sort_keys=True))
        if key not in self._grammars:
            if vocab_size not in self._compilers:
                _, tokenizer = self.chat_slot().obj
                info = xgr.TokenizerInfo.from_huggingface(
                    tokenizer._tokenizer,
                    vocab_size=vocab_size,
                    stop_token_ids=list(tokenizer.eos_token_ids),
                )
                self._compilers[vocab_size] = xgr.GrammarCompiler(info)
            # Compact JSON only. With xgrammar's default (any whitespace) a
            # small model can spend its whole token budget on newlines and
            # never close the object; one fixed separator style removes that
            # failure, and nothing downstream cares about formatting.
            self._grammars[key] = self._compilers[vocab_size].compile_json_schema(
                json.dumps(schema), any_whitespace=False
            )
        return self._grammars[key]

    def remember_prefix(self, tokens: list[int], cache) -> None:
        """Keep a finished generation's KV for the next request that shares
        its prefix, keyed by exactly the tokens the cache holds.

        The two drift apart in both directions, and a key that disagrees with
        its cache poisons every later request that reuses it: the trie trims
        by key length, so any extra position survives the trim and the next
        answer is generated after a token from someone else's. (Found
        2026-10-04: entity names from earlier requests -- "AliceQdrant" --
        leaking into graph extraction.)

        - The cache can hold *more*: mlx-lm computes one step ahead, so when
          generation stops the token after the last one kept (often the stop
          token) may already be in it. Trimmed back to the tokens we know.
        - The cache can hold *fewer*: the last sampled token was never run
          through the model. The key is cut to what the cache holds.
        """
        from mlx_lm.models.cache import can_trim_prompt_cache, trim_prompt_cache

        slot = self.slots["chat"]
        if slot.prompt_cache is None or not cache:
            return
        held = getattr(cache[0], "offset", None)
        if not held:
            return
        if held > len(tokens):
            if not can_trim_prompt_cache(cache):
                return
            trim_prompt_cache(cache, held - len(tokens))
            held = len(tokens)
        slot.prompt_cache.insert_cache(slot.repo, tokens[:held], cache)

    def encode_chat(self, messages: list[dict], enable_thinking: bool) -> list[int]:
        _, tokenizer = self.chat_slot().obj
        return tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=enable_thinking,
        )

    async def chat(
        self, messages: list[dict], params: GenParams, priority: Priority
    ) -> AsyncIterator[Event]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        # Tokenising is cheap and needs no GPU; doing it here keeps the
        # worker thread for model work only.
        prompt = self.encode_chat(messages, params.enable_thinking)
        job = GenerationJob(self, prompt, params, priority, loop, queue)
        self.scheduler.submit(job)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.kind in ("done", "error"):
                    return
        finally:
            # A closed stream or a cancelled request stops the job at its
            # next step instead of generating into the void.
            job.cancelled = True

    # ── decisions ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(answer: dict) -> dict:
        """Laya's answer reshaped to what core reads: ``p`` for yes/no,
        ``label`` and ``probs`` for a choice, ``score`` for a scale."""
        out: dict[str, Any] = {"type": answer.get("type"), "confidence": answer.get("confidence")}
        if answer.get("type") == "noul":
            out["p"] = answer.get("noul")
        elif answer.get("type") == "choice":
            out["label"] = answer.get("choice")
            out["probs"] = answer.get("probabilities")
        else:
            out["score"] = answer.get("score")
            out["probs"] = answer.get("probabilities")
            out["legend"] = answer.get("legend")
        return out

    def _decide_now(self, state: Any, questions: dict) -> dict:
        slot = self.slots.get("decide")
        if slot is None or not slot.loaded:
            raise ModelNotLoaded("the decision model is not loaded")
        result = slot.obj.predict(state, questions)
        return {
            "answers": {qid: self._normalise(a) for qid, a in result["answers"].items()},
            "usage": result.get("usage", {}),
        }

    async def decide(self, state: Any, questions: dict) -> dict:
        # Live: decisions sit on the path of a turn, and take milliseconds.
        return await self.scheduler.call(lambda: self._decide_now(state, questions), Priority.LIVE)

    # ── voice ────────────────────────────────────────────────────────────────

    def _ensure(self, name: str):
        """Session models load on first use; touching one keeps it resident."""
        slot = self.slots[name]
        if not slot.loaded:
            self._load(name)
        self.last_used[name] = time.monotonic()
        return slot.obj

    def _transcribe_now(self, pcm16: bytes) -> dict:
        import mlx.core as mx
        import numpy as np
        from parakeet_mlx.audio import get_logmel

        stt = self._ensure("stt")
        audio = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        seconds = len(audio) / stt.preprocessor_config.sample_rate
        if len(audio) < stt.preprocessor_config.hop_length * 4:
            return {"text": "", "seconds": round(seconds, 3)}
        # float32 for the spectrogram: in bfloat16 the FFT's shape comes out wrong.
        mel = get_logmel(mx.array(audio), stt.preprocessor_config)
        result = stt.generate(mel)[0]
        return {"text": result.text.strip(), "seconds": round(seconds, 3)}

    async def transcribe(self, pcm16: bytes) -> dict:
        """16 kHz mono PCM16 in, text out. Live: a person is waiting."""
        return await self.scheduler.call(lambda: self._transcribe_now(pcm16), Priority.LIVE)

    def _speak_now(self, text: str, voice: str | None, speed: float) -> bytes:
        import numpy as np

        tts = self._ensure("tts")
        parts = [np.array(chunk.audio).reshape(-1) for chunk in
                 tts.generate(text=text, voice=voice or self.settings.tts_voice, speed=speed, lang_code="a")]
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    async def speak(self, text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
        """Text in, 24 kHz mono PCM16 out."""
        return await self.scheduler.call(lambda: self._speak_now(text, voice, speed), Priority.LIVE)

    async def reap_idle(self) -> list[str]:
        """Unload session models nobody has used for a while."""
        now = time.monotonic()
        idle = [n for n, s in self.slots.items()
                if s.lifetime == "session" and s.loaded
                and now - self.last_used.get(n, 0) > self.settings.session_idle_seconds]
        for name in idle:
            await self.unload(name)
        return idle

    # ── embeddings ───────────────────────────────────────────────────────────

    def _embed_now(self, texts: list[str], dimensions: int | None) -> tuple[list[list[float]], int]:
        import mlx.core as mx

        slot = self.slots["embed"]
        if not slot.loaded:
            raise ModelNotLoaded(f"embedding model {slot.repo} is not loaded")
        model, tokenizer = slot.obj
        vectors: list[list[float]] = []
        used = 0
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            enc = tokenizer(batch, return_tensors="mlx", padding=True, truncation=True, max_length=2048)
            emb = model(enc["input_ids"], attention_mask=enc["attention_mask"]).text_embeds
            if dimensions and dimensions < emb.shape[-1]:
                # Matryoshka truncation, then back to unit length.
                emb = emb[:, :dimensions]
                emb = emb / mx.linalg.norm(emb, axis=-1, keepdims=True)
            mx.eval(emb)
            vectors.extend(emb.tolist())
            used += int(enc["attention_mask"].sum().item())
        return vectors, used

    async def embed(self, texts: list[str], dimensions: int | None) -> tuple[list[list[float]], int]:
        # Always live: a query embedding sits on the path of every turn, and
        # an embedding batch is milliseconds either way.
        return await self.scheduler.call(lambda: self._embed_now(texts, dimensions), Priority.LIVE)

    # ── reporting ────────────────────────────────────────────────────────────

    def _describe(self, slot: ModelSlot) -> dict:
        return {
            "name": slot.name,
            "model": slot.repo,
            "lifetime": slot.lifetime,
            "loaded": slot.loaded,
            "weights_gb": round(slot.nbytes / 1024**3, 3),
            "prompt_cache_gb": round(slot.prompt_cache.nbytes / 1024**3, 3)
            if slot.prompt_cache is not None else 0.0,
        }

    def status(self) -> dict:
        import mlx.core as mx

        return {
            "ready": self.ready,
            "models": [self._describe(s) for s in self.slots.values()],
            "active_memory_gb": round(mx.get_active_memory() / 1024**3, 3),
            "buffer_cache_gb": round(mx.get_cache_memory() / 1024**3, 3),
            "peak_memory_gb": round(mx.get_peak_memory() / 1024**3, 3),
            "queue": self.scheduler.queue_depth(),
            "busy_with": self.scheduler.busy_with.name.lower() if self.scheduler.busy_with is not None else None,
        }
