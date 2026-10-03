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
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import chat_title
from .memory_client import Fact, MemoryClient
from .models import Chat, Message
from .orchestrator import Attachment, Route, select_route, wants_web_search
from .providers.base import Message as ProviderMessage
from .providers.base import Provider
from .rag_client import RagClient, RagResult, RagSource
from .web_client import WebClient, WebSource

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Nexus, a local-first assistant. Answer directly and concretely. "
    "Prefer specifics over generalities, admit what you do not know, and keep "
    "formatting plain unless the answer genuinely needs structure."
)

# How many prior turns to replay. Cheap insurance against an unbounded prompt on
# a long thread; a real budget in tokens replaces it once retrieval competes for
# the same context window (phase 4).
HISTORY_LIMIT = 40

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
    web_sources: tuple[WebSource, ...] = ()


def _derive_title(content: str) -> str:
    """First line of the opening message, clipped at a word boundary."""
    first_line = content.strip().splitlines()[0].strip()
    if len(first_line) <= TITLE_MAX_CHARS:
        return first_line
    clipped = first_line[:TITLE_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{clipped or first_line[:TITLE_MAX_CHARS]}…"


async def _load_history(session: AsyncSession, chat_id: uuid.UUID) -> list[Message]:
    """The most recent turns, returned oldest first."""
    result = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(list(result.scalars())))


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


def render_web_context(sources: Sequence[WebSource]) -> str:
    """Live web results as a system message, same framing rule as
    render_memory/render_rag_context: material to use, not instructions,
    explicitly allowed to be irrelevant. Includes the URL per result so the
    model can cite what it's drawing from, the one context source here that
    plausibly needs a citation a person could actually go check."""
    lines = "\n".join(f"- {s.title} ({s.url}): {s.snippet}" for s in sources)
    return (
        "Web search results, relevant to what was just asked:\n"
        f"{lines}\n\n"
        "Use them where they answer the question, and cite the URL when you "
        "do. If none of them are actually relevant, say so rather than "
        "guessing from them anyway."
    )


def build_prompt(
    history: list[Message],
    facts: Sequence[Fact] = (),
    rag_result: RagResult | None = None,
    web_sources: Sequence[WebSource] = (),
) -> list[ProviderMessage]:
    """The system prompt, then recalled memory, then retrieved documents,
    then live web results, then the conversation.

    All three context sources go above the transcript so the turn the user
    just typed stays the most recent thing in the window, and stays what the
    model is answering. Memory first (durable, about the person), then RAG
    (specific to this project's documents), then the web (specific to this
    one question, and the only source that isn't local) — broad to narrow,
    right before the live conversation.
    """
    prompt: list[ProviderMessage] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if facts:
        prompt.append({"role": "system", "content": render_memory(facts)})
    if rag_result is not None and rag_result.prompt:
        prompt.append({"role": "system", "content": render_rag_context(rag_result)})
    if web_sources:
        prompt.append({"role": "system", "content": render_web_context(web_sources)})
    for message in history:
        if message.role == "system":
            continue
        prompt.append({"role": message.role, "content": message.content})
    return prompt


async def generate_turn(
    session: AsyncSession,
    *,
    chat: Chat,
    content: str,
    provider: Provider,
    user_id: uuid.UUID,
    memory: MemoryClient | None = None,
    rag: RagClient | None = None,
    web: WebClient | None = None,
    attachments: tuple[Attachment, ...] = (),
    title_model: str | None = None,
) -> Turn:
    """Store the user message, answer it, store the answer.

    Both messages are written even if generation fails — the transcript records
    what was said, and a turn that errored is still something that was said.
    """
    route = await select_route(
        content=content,
        attachments=attachments,
        rag=rag,
        user_id=user_id,
        project_id=chat.project_id,
    )

    user_message = Message(
        chat_id=chat.id,
        role="user",
        content=content,
        meta={"route": route.handler},
    )
    session.add(user_message)
    await session.flush()

    # Kicked off here, not awaited until after the answer call below, so a
    # good title costs nothing in wall-clock time: it runs alongside
    # generation instead of before or after it.
    is_new_chat = chat.title == DEFAULT_CHAT_TITLE
    title_task = (
        asyncio.create_task(
            chat_title.generate(provider, user_message=content, model=title_model)
        )
        if is_new_chat
        else None
    )

    history = await _load_history(session, chat.id)

    # Retrieval runs against what the user just said, before generation. It is
    # allowed to come back empty -- an unreachable memory service degrades the
    # answer, it does not fail the turn.
    facts: list[Fact] = []
    if memory is not None:
        try:
            facts = await memory.search(
                content, user_id=user_id, project_id=chat.project_id
            )
        except Exception:
            # The client already swallows transport failures; this catches
            # everything else, because the rule is that memory degrades an
            # answer and never fails one.
            logger.exception("Memory search raised for chat=%s", chat.id)

    # Same rule as memory: RAG being unreachable degrades this answer, it does
    # not fail the turn. Only called at all when the route said "rag" — the
    # existence check in select_route() already decided it's worth trying.
    rag_result: RagResult | None = None
    if route.handler == "rag" and rag is not None:
        try:
            rag_result = await rag.query(content, user_id=user_id, project_id=chat.project_id)
        except Exception:
            # rag.query() already catches its own transport failures and
            # returns None; this catches anything else, same reasoning as
            # the memory search above.
            logger.exception("RAG query raised for chat=%s", chat.id)

    # Independent of route.handler entirely -- this can fire alongside
    # "general" or "rag" alike, the way memory search does. Unlike memory
    # and RAG, it never runs unasked: everything else here works offline,
    # and web search is the deliberate exception, not a default.
    web_sources: tuple[WebSource, ...] = ()
    if web is not None and wants_web_search(content):
        try:
            result = await web.search(content)
            if result:
                web_sources = result
        except Exception:
            # web.search() already catches its own transport failures and
            # returns None; this catches anything else, same reasoning as
            # the memory search and RAG query above.
            logger.exception("Web search raised for chat=%s", chat.id)

    prompt = build_prompt(history, facts, rag_result, web_sources)

    started = time.perf_counter()
    error: str | None = None
    try:
        answer = await provider.chat(prompt)
    except Exception as exc:
        logger.exception("Generation failed for chat=%s", chat.id)
        error = f"{exc.__class__.__name__}: {exc}"
        answer = (
            "I couldn't reach the model to answer that. The message is saved — "
            "try again once the provider is reachable."
        )
    latency_ms = round((time.perf_counter() - started) * 1000)

    if title_task is not None:
        smart_title = await title_task
        # Falls back to the cheap first-line derivation if the model call
        # failed or came back empty -- a chat still needs *a* name, even a
        # bad one, rather than sitting on the placeholder forever.
        chat.title = smart_title or _derive_title(content)

    meta: dict = {
        "route": route.handler,
        "route_reason": route.reason,
        "latency_ms": latency_ms,
        "prompt_messages": len(prompt),
        # What was recalled, so the UI can show it and so a bad answer can be
        # traced back to what the model was told.
        "recalled_facts": [
            {"id": f.id, "fact": f.fact, "scope": f.scope, "kind": f.kind}
            for f in facts
        ],
        "rag_sources": [
            {
                "file_name": s.file_name,
                "page_number": s.page_number,
                "chunk_index": s.chunk_index,
                "heading_path": s.heading_path,
            }
            for s in (rag_result.sources if rag_result else ())
        ],
        "web_sources": [
            {"title": s.title, "url": s.url, "snippet": s.snippet}
            for s in web_sources
        ],
    }
    if error is not None:
        meta["error"] = error

    assistant_message = Message(
        chat_id=chat.id,
        role="assistant",
        content=answer,
        meta=meta,
    )
    session.add(assistant_message)
    await session.flush()

    logger.info(
        "turn chat=%s handler=%s latency_ms=%d history=%d recalled=%d rag_sources=%d web_sources=%d%s",
        chat.id,
        route.handler,
        latency_ms,
        len(history),
        len(facts),
        len(rag_result.sources) if rag_result else 0,
        len(web_sources),
        " FAILED" if error else "",
    )
    return Turn(
        user_message=user_message,
        assistant_message=assistant_message,
        route=route,
        facts=tuple(facts),
        rag_sources=rag_result.sources if rag_result else (),
        web_sources=web_sources,
    )
