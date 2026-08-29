"""Turn a user message into an answer.

The whole generation path lives here so the router stays a thin HTTP shell and
so phase 4 has one obvious place to splice retrieval in: between building the
history and calling the provider.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chat, Message
from .orchestrator import Attachment, Route, select_route
from .providers.base import Message as ProviderMessage
from .providers.base import Provider

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


def build_prompt(history: list[Message]) -> list[ProviderMessage]:
    """History plus the system prompt, in the shape a provider wants.

    Retrieved memory joins this list in phase 4, after the system prompt and
    before the conversation.
    """
    prompt: list[ProviderMessage] = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    attachments: tuple[Attachment, ...] = (),
) -> Turn:
    """Store the user message, answer it, store the answer.

    Both messages are written even if generation fails — the transcript records
    what was said, and a turn that errored is still something that was said.
    """
    route = select_route(content=content, attachments=attachments)

    user_message = Message(
        chat_id=chat.id,
        role="user",
        content=content,
        meta={"route": route.handler},
    )
    session.add(user_message)
    await session.flush()

    if chat.title == DEFAULT_CHAT_TITLE:
        chat.title = _derive_title(content)

    history = await _load_history(session, chat.id)
    prompt = build_prompt(history)

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

    meta: dict = {
        "route": route.handler,
        "route_reason": route.reason,
        "latency_ms": latency_ms,
        "prompt_messages": len(prompt),
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
        "turn chat=%s handler=%s latency_ms=%d history=%d%s",
        chat.id,
        route.handler,
        latency_ms,
        len(history),
        " FAILED" if error else "",
    )
    return Turn(
        user_message=user_message,
        assistant_message=assistant_message,
        route=route,
    )
