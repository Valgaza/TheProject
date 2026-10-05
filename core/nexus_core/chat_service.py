"""Turn a user message into an answer.

The whole generation path lives here so the router stays a thin HTTP shell and
so phase 4 has one obvious place to splice retrieval in: between building the
history and calling the provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_title
from .memory_client import Fact, MemoryClient
from .models import Chat, Message, Profile
from .orchestrator import Attachment, Route, select_route
from .providers.base import Message as ProviderMessage
from .providers.base import Provider
from .rag_client import RagClient, RagResult, RagSource
from .web_client import WEB_TRIGGER, WebClient, WebSearch
from .decisions import WEB_INTENT_QUESTIONS, DecisionsClient, log_entry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Nexus, a local-first assistant. Answer directly and concretely. "
    "Prefer specifics over generalities, admit what you do not know, and keep "
    "formatting plain unless the answer genuinely needs structure."
)

# Between the context attached to a message and the message itself: the
# context is the system's, not something the person wrote.
CONTEXT_DIVIDER = "--- The message, from the person ---"

# How many prior turns to replay. Cheap insurance against an unbounded prompt on
# a long thread; a real budget in tokens replaces it once retrieval competes for
# the same context window (phase 4).
HISTORY_LIMIT = 40
HISTORY_STEP = 10

# Nothing in the schema says a title has to be short, but the sidebar does.
TITLE_MAX_CHARS = 60
DEFAULT_CHAT_TITLE = "New conversation"


@dataclass(frozen=True)
class Turn:
    user_message: Message
    assistant_message: Message
    route: Route
    facts: tuple[Fact, ...] = ()
    rag_sources: tuple[RagSource, ...] = ()


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _derive_title(content: str) -> str:
    """First line of the opening message, clipped at a word boundary."""
    first_line = content.strip().splitlines()[0].strip()
    if len(first_line) <= TITLE_MAX_CHARS:
        return first_line
    clipped = first_line[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{clipped or first_line[:TITLE_MAX_CHARS]}…"


async def _load_history(session: AsyncSession, chat_id: uuid.UUID) -> list[Message]:
    """The most recent turns, returned oldest first.

    Past HISTORY_LIMIT the window moves HISTORY_STEP messages at a time
    rather than one: every move changes where the prompt starts, which throws
    away the runtime's cached prefix for the whole conversation. Sliding by
    one message did that on every turn of a long conversation.
    """
    total = await session.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id))
    over = max(0, (total or 0) - HISTORY_LIMIT)
    skip = -(-over // HISTORY_STEP) * HISTORY_STEP  # rounded up to a whole step
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at, Message.id)
        .offset(skip)
    )
    return list(result.scalars())


def render_memory(facts: Sequence[Fact]) -> str:
    """Recalled facts as a system message.

    Framed as things already known rather than as instructions, and explicitly
    fallible. Presented as ground truth, a model will defend a stale fact
    against the user correcting it, which is exactly the failure that makes
    memory feel broken rather than helpful.
    """
    lines = "\n".join(f"- {fact.render()}" for fact in facts)
    return (
        "What you already know about this person and their work, recalled from "
        "earlier conversations:\n"
        f"{lines}\n\n"
        "Use it where it is relevant and say nothing about it where it is not. "
        "It may be incomplete or out of date; if the user contradicts it, they "
        "are right."
    )


def render_rag_context(rag_result: RagResult) -> str:
    """Retrieved document passages as a system message, same framing rule as
    render_memory: presented as material to use, not as instructions, and
    explicitly allowed to be irrelevant."""
    return (
        "Passages retrieved from this project's documents, relevant to what "
        "was just asked:\n"
        f"{rag_result.prompt}\n\n"
        "Use them where they answer the question. If they don't, say so rather "
        "than guessing from them anyway."
    )


def render_web_context(web: WebSearch) -> str:
    """Search results as a system message -- or, when the search did not run,
    an instruction to say so. A model told nothing answers from its training
    data as if it had looked, which is worse than admitting it could not."""
    if not web.results:
        return (
            "The person asked for a web search, but it did not run "
            f"({web.error or 'no results'}). Say plainly that you could not "
            "check the web, then answer from what you know if you can."
        )
    lines = "\n\n".join(
        f"[{i}] {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(web.results, 1)
    )
    return (
        f"Web search results for \"{web.query_sent}\":\n\n{lines}\n\n"
        "Use them where they answer the question and cite them by number, "
        "e.g. [2]. They are from the open web: weigh them, don't trust them."
    )


def build_prompt(
    history: list[Message],
    facts: Sequence[Fact] = (),
    rag_result: RagResult | None = None,
    web: WebSearch | None = None,
) -> list[ProviderMessage]:
    """The system prompt, the conversation so far, then the message being
    answered with what was recalled and retrieved for it.

    The context travels with the latest message rather than above the
    transcript: it changes every turn, and anything that changes ends the
    prefix the runtime can reuse from the previous turn's prompt cache. This
    way the system prompt and every earlier turn are a stable prefix, so a
    long conversation is not prefilled again each time (measured on the voice
    bench: first-token time had been growing with every exchange).

    It is part of the user message, not a system message of its own, because
    several models' chat templates (Qwen3.5, Gemma) refuse a system message
    anywhere but first. Within it, memory first (durable, about the person),
    then documents and the web (specific to this question): broad to narrow.
    """
    prompt: list[ProviderMessage] = [{"role": "system", "content": SYSTEM_PROMPT}]
    context: list[str] = []
    if facts:
        context.append(render_memory(facts))
    if rag_result is not None and rag_result.prompt:
        context.append(render_rag_context(rag_result))
    if web is not None:
        context.append(render_web_context(web))

    turns = [m for m in history if m.role != "system"]
    latest = turns[-1] if turns and turns[-1].role == "user" else None
    for message in turns[:-1] if latest is not None else turns:
        prompt.append({"role": message.role, "content": message.content})
    if latest is None:
        # Nothing to attach it to (not a real turn); keep it next to the
        # system prompt.
        prompt[1:1] = [{"role": "system", "content": block} for block in context]
    elif context:
        prompt.append({"role": "user", "content": "\n\n".join(context)
                       + f"\n\n{CONTEXT_DIVIDER}\n{latest.content}"})
    else:
        prompt.append({"role": "user", "content": latest.content})
    return prompt


@dataclass
class PreparedTurn:
    """Everything decided before the model is asked: what was said, what was
    recalled and retrieved, and the exact prompt. Split out so a streamed
    answer and a buffered one share every step but the model call."""

    chat: Chat
    content: str
    user_id: uuid.UUID
    route: Route
    user_message: Message
    history: list[Message]
    facts: list[Fact]
    rag_result: RagResult | None
    web_search: WebSearch | None
    prompt: list[ProviderMessage]
    title_task: asyncio.Task | None
    timings: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)


async def _timed(timings: dict[str, int], key: str, coro):
    started = time.perf_counter()
    try:
        return await coro
    finally:
        timings[key] = _ms_since(started)


async def _search_memory(memory: MemoryClient, content: str, user_id, chat: Chat) -> list[Fact]:
    # Retrieval is allowed to come back empty -- an unreachable memory service
    # degrades the answer, it does not fail the turn.
    try:
        return await memory.search(content, user_id=user_id, project_id=chat.project_id)
    except Exception:
        # The client already swallows transport failures; this catches
        # everything else, because the rule is that memory degrades an
        # answer and never fails one.
        logger.exception("Memory search raised for chat=%s", chat.id)
        return []


async def _query_documents(
    rag: RagClient, content: str, user_id, chat: Chat, rag_min_score: float, timings: dict[str, int]
) -> RagResult | None:
    # Documents are a retrieval tier, not a route -- the same shape as memory,
    # and for the same reason. Every turn queries them and a relevance floor
    # decides per turn whether the corpus had anything to say. Routing on
    # "does this project have documents" was the previous design: once a
    # project held one file, every turn went through retrieval, "thanks"
    # included.
    try:
        candidate = await rag.query(content, user_id=user_id, project_id=chat.project_id)
    except Exception:
        # rag.query() already catches its own transport failures and returns
        # None; this catches anything else, same reasoning as memory.
        logger.exception("RAG query raised for chat=%s", chat.id)
        return None
    if candidate is None:
        return None
    if candidate.elapsed_seconds is not None:
        # The pipeline's own time, so the HTTP hop shows up as the difference.
        timings["rag_service_ms"] = round(candidate.elapsed_seconds * 1000)
    if not candidate.sources:
        return None
    if candidate.best_score >= rag_min_score:
        return candidate
    logger.info(
        "rag below floor chat=%s best=%.3f floor=%.3f sources=%d",
        chat.id, candidate.best_score, rag_min_score, len(candidate.sources),
    )
    return None


async def _search_web(
    web: WebClient | None, content: str, known_names: list[str], user_id, chat: Chat,
) -> WebSearch:
    # Only when asked for, and only through the websearch service, which
    # redacts: what goes out is its rewrite, never the raw message.
    if web is None:
        return WebSearch(query_sent=None, error="web search is not configured")
    try:
        return await web.search(content, known_names=known_names, user_id=str(user_id))
    except Exception:
        # WebClient.search() already turns its own failures into an error;
        # this catches anything else, same as memory and RAG.
        logger.exception("Web search raised for chat=%s", chat.id)
        return WebSearch(query_sent=None, error="web search failed")


async def prepare_turn(
    session: AsyncSession,
    *,
    chat: Chat,
    content: str,
    provider: Provider,
    user_id: uuid.UUID,
    memory: MemoryClient | None = None,
    rag: RagClient | None = None,
    attachments: tuple[Attachment, ...] = (),
    title_model: str | None = None,
    rag_min_score: float = 0.5,
    web: WebClient | None = None,
    use_web: bool = False,
    decisions: DecisionsClient | None = None,
    laya_web_intent: str = "off",
    laya_web_intent_above: float = 0.9,
) -> PreparedTurn:
    """Store the user message and gather everything the answer needs."""
    turn_started = time.perf_counter()
    # Wall-clock per stage, in ms, stored on the answer as meta.timings. Every
    # latency change in the milestone is judged against these numbers.
    timings: dict[str, int] = {}

    # Only asked when nothing cheaper has already decided: the button and the
    # trigger phrases settle most web requests without a model.
    intent = None
    ask_laya = (
        decisions is not None and laya_web_intent in ("shadow", "live")
        and not use_web and not WEB_TRIGGER.search(content)
    )
    shadow_intent: asyncio.Task | None = None
    if ask_laya and laya_web_intent == "live":
        stage = time.perf_counter()
        intent = await decisions.decide({"message": content}, WEB_INTENT_QUESTIONS)
        timings["laya_ms"] = _ms_since(stage)
    elif ask_laya:
        # In shadow its answer cannot change the route, so it is not waited
        # for: it runs alongside retrieval and is logged once that is done.
        shadow_intent = asyncio.create_task(decisions.decide({"message": content}, WEB_INTENT_QUESTIONS))

    route = await select_route(
        content=content, attachments=attachments, rag=rag, use_web=use_web,
        web_intent_p=intent.p("asks_for_web") if intent and laya_web_intent == "live" else None,
        web_intent_above=laya_web_intent_above,
    )

    user_message = Message(chat_id=chat.id, role="user", content=content, meta={"route": route.handler})
    session.add(user_message)
    await session.flush()

    def log_intent(decision) -> None:
        # What the existing logic would have done without Laya, so shadow
        # agreement can be measured: anything not caught above is "general".
        session.add(log_entry(
            point="web_intent", mode=laya_web_intent, state={"message": content},
            decision=decision, baseline={"asks_for_web": False, "route": route.handler},
            chat_id=chat.id, message_id=user_message.id,
        ))

    if ask_laya and shadow_intent is None:
        log_intent(intent)

    # Kicked off here, not awaited until after the answer, so a good title
    # costs nothing in wall-clock time: it runs alongside generation.
    title_task = (
        asyncio.create_task(chat_title.generate(provider, user_message=content, model=title_model))
        if chat.title == DEFAULT_CHAT_TITLE
        else None
    )

    stage = time.perf_counter()
    history = await _load_history(session, chat.id)
    timings["history_ms"] = _ms_since(stage)

    known_names: list[str] = []
    if route.handler == "web":
        profile = await session.get(Profile, user_id)
        known_names = [n for n in (getattr(profile, "display_name", None),
                                   getattr(profile, "email", None)) if n]

    # Memory, documents and (when asked for) the web are independent of each
    # other, so they run at the same time: the turn waits for the slowest,
    # not for the sum.
    async def nothing():
        return None

    facts, rag_result, web_search = await asyncio.gather(
        _timed(timings, "memory_ms", _search_memory(memory, content, user_id, chat))
        if memory is not None else nothing(),
        _timed(timings, "rag_ms", _query_documents(rag, content, user_id, chat, rag_min_score, timings))
        if rag is not None else nothing(),
        _timed(timings, "web_ms", _search_web(web, content, known_names, user_id, chat))
        if route.handler == "web" else nothing(),
    )
    facts = facts or []
    if shadow_intent is not None:
        log_intent(await shadow_intent)

    # The route recorded is what actually happened, not what was predicted
    # before retrieval ran. "rag" means documents genuinely informed this
    # answer -- they cleared the floor -- which is the only version of the
    # label a person can act on. A web turn keeps its label even when
    # documents also helped: it is the one route that says words left the
    # machine.
    if rag_result is not None and route.handler != "web":
        n = len(rag_result.sources)
        route = Route(
            handler="rag",
            reason=f"{n} document {'chunk' if n == 1 else 'chunks'} cleared the relevance floor "
                   f"(best {rag_result.best_score:.2f})",
        )

    prompt = build_prompt(history, facts, rag_result, web_search)
    return PreparedTurn(
        chat=chat, content=content, user_id=user_id, route=route, user_message=user_message,
        history=history, facts=facts, rag_result=rag_result, web_search=web_search,
        prompt=prompt, title_task=title_task, timings=timings, started=turn_started,
    )


FAILED_ANSWER = (
    "I couldn't reach the model to answer that. The message is saved — "
    "try again once the provider is reachable."
)


async def finalize_turn(
    session: AsyncSession,
    prepared: PreparedTurn,
    *,
    answer: str,
    error: str | None,
    generation_ms: int,
    first_token_ms: int | None = None,
    interrupted: bool = False,
) -> Turn:
    """Store the answer with everything that explains it, and finish the title."""
    timings = prepared.timings
    if first_token_ms is not None:
        # From the start of the turn to the first visible token: what a
        # person actually waits through before anything appears.
        timings["first_token_ms"] = first_token_ms
    timings["generation_ms"] = generation_ms
    chat = prepared.chat

    if prepared.title_task is not None:
        stage = time.perf_counter()
        smart_title = await prepared.title_task
        # Only the wait left after the answer: the title ran alongside it.
        timings["title_wait_ms"] = _ms_since(stage)
        # Falls back to the cheap first-line derivation if the model call
        # failed or came back empty -- a chat still needs *a* name.
        chat.title = smart_title or _derive_title(prepared.content)

    timings["total_ms"] = _ms_since(prepared.started)
    rag_result, web_search, facts = prepared.rag_result, prepared.web_search, prepared.facts
    meta: dict = {
        "route": prepared.route.handler,
        "route_reason": prepared.route.reason,
        "latency_ms": generation_ms,
        "timings": timings,
        "prompt_messages": len(prepared.prompt),
        # What was recalled, so the UI can show it and so a bad answer can be
        # traced back to what the model was told.
        "recalled_facts": [
            {"id": f.id, "fact": f.fact, "scope": f.scope, "kind": f.kind} for f in facts
        ],
        "rag_sources": [
            {
                "file_name": s.file_name,
                "page_number": s.page_number,
                "chunk_index": s.chunk_index,
                "heading_path": s.heading_path,
                "rerank_score": round(s.rerank_score, 4),
            }
            for s in (rag_result.sources if rag_result else ())
        ],
    }
    if web_search is not None:
        # The exact text that left the machine, so anyone can check it.
        meta["web"] = {
            "query_sent": web_search.query_sent,
            "error": web_search.error,
            "results": [{"title": r.title, "url": r.url} for r in web_search.results],
        }
    if interrupted:
        # Cut off by the person (a closed stream, or later a spoken
        # interruption): what is stored is what they actually received.
        meta["interrupted"] = True
    if error is not None:
        meta["error"] = error

    assistant_message = Message(chat_id=chat.id, role="assistant", content=answer, meta=meta)
    session.add(assistant_message)
    await session.flush()

    logger.info(
        "turn chat=%s handler=%s latency_ms=%d history=%d recalled=%d rag_sources=%d timings=%s%s",
        chat.id, prepared.route.handler, generation_ms, len(prepared.history), len(facts),
        len(rag_result.sources) if rag_result else 0,
        " ".join(f"{k.removesuffix('_ms')}={v}" for k, v in timings.items()),
        " FAILED" if error else "",
    )
    return Turn(
        user_message=prepared.user_message,
        assistant_message=assistant_message,
        route=prepared.route,
        facts=tuple(facts),
        rag_sources=rag_result.sources if rag_result else (),
    )


async def generate_turn(session: AsyncSession, *, provider: Provider, **kwargs) -> Turn:
    """Store the user message, answer it, store the answer -- buffered.

    Both messages are written even if generation fails: the transcript records
    what was said, and a turn that errored is still something that was said.
    """
    prepared = await prepare_turn(session, provider=provider, **kwargs)
    started = time.perf_counter()
    error: str | None = None
    try:
        # "live": the one call in a turn that someone is watching.
        answer = await provider.chat(prepared.prompt, priority="live")
    except Exception as exc:
        logger.exception("Generation failed for chat=%s", prepared.chat.id)
        error = f"{exc.__class__.__name__}: {exc}"
        answer = FAILED_ANSWER
    return await finalize_turn(
        session, prepared, answer=answer, error=error, generation_ms=_ms_since(started)
    )


@dataclass
class TurnEvent:
    kind: str  # "prepared" | "token" | "done"
    prepared: PreparedTurn | None = None
    text: str = ""
    turn: Turn | None = None


async def stream_turn(session: AsyncSession, *, provider: Provider, **kwargs) -> AsyncIterator[TurnEvent]:
    """The same turn as generate_turn, with the answer yielded as it is made.

    Yields "prepared" once retrieval is done (so sources can show before the
    first word), "token" per piece of text, then "done" with the stored turn.
    If the consumer goes away mid-answer, whatever was already produced is
    still stored, marked interrupted.
    """
    prepared = await prepare_turn(session, provider=provider, **kwargs)
    yield TurnEvent("prepared", prepared=prepared)

    started = time.perf_counter()
    parts: list[str] = []
    first_token_ms: int | None = None
    error: str | None = None
    interrupted = False
    try:
        async for piece in provider.chat_stream(prepared.prompt, priority="live"):
            if not piece:
                continue
            if first_token_ms is None:
                first_token_ms = _ms_since(prepared.started)
            parts.append(piece)
            yield TurnEvent("token", text=piece)
    except (asyncio.CancelledError, GeneratorExit):
        interrupted = True
        raise
    except Exception as exc:
        logger.exception("Generation failed for chat=%s", prepared.chat.id)
        error = f"{exc.__class__.__name__}: {exc}"
        if not parts:
            parts = [FAILED_ANSWER]
            yield TurnEvent("token", text=FAILED_ANSWER)
    finally:
        if interrupted:
            # Store what was said before the interruption. Shielded so the
            # cancellation that ended the stream does not also lose the row.
            async def store():
                await finalize_turn(
                    session, prepared, answer="".join(parts), error=None,
                    generation_ms=_ms_since(started), first_token_ms=first_token_ms,
                    interrupted=True,
                )
                await session.commit()
            try:
                await asyncio.shield(store())
            except Exception:
                logger.exception("could not store an interrupted turn for chat=%s", prepared.chat.id)

    turn = await finalize_turn(
        session, prepared, answer="".join(parts), error=error,
        generation_ms=_ms_since(started), first_token_ms=first_token_ms,
    )
    yield TurnEvent("done", turn=turn)
