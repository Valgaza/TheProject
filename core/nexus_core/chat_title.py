"""Naming a chat from what it's actually about, not its first few words.

Same shape as write_gate.py's decision: its own cheap model call, never on
the request path in the sense of adding latency -- generate_turn() kicks
this off alongside the answer call (see chat_service.py) rather than before
or after it, so a good title costs nothing in wall-clock time on top of the
answer the user is already waiting for. Falls back to a plain first-line
derivation if the call fails or returns nothing usable; a chat still needs
*a* name either way.
"""

from __future__ import annotations

import logging

from .providers.base import Provider

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = (
    "Write a short title (3-6 words) for a chat that opens with the message "
    "below. Describe what it's about, the way ChatGPT or Claude name a "
    "conversation -- not a summary, not a restatement of the question. "
    "Plain text only: no quotes, no trailing punctuation, no prefix like "
    "'Title:'."
)

TITLE_MAX_CHARS = 60


async def generate(
    provider: Provider,
    *,
    user_message: str,
    model: str | None = None,
) -> str | None:
    """A short, topical title for a new chat, or None if generation failed
    or came back unusable. Never raises."""
    try:
        raw = await provider.chat(
            [
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            model=model,
            # Generous for a 3-6 word title on purpose: a "thinking" model
            # (Gemini 3.x, confirmed live 2026-09-02) spends part of this
            # budget on invisible reasoning before it writes anything, and
            # that budget varies call to call -- 20 reproduced empty output
            # every time, 150 was sometimes enough and sometimes still cut
            # the visible title off mid-word. 400 is comfortably above what
            # was observed needed; still a small, fast call in absolute terms.
            max_tokens=400,
        )
    except Exception as exc:
        logger.warning("Chat title generation failed: %s", exc)
        return None

    # Models asked for "plain text only" still sometimes wrap it in quotes.
    title = raw.strip().strip('"').strip("'").strip()
    if not title:
        return None
    if len(title) > TITLE_MAX_CHARS:
        clipped = title[:TITLE_MAX_CHARS]
        last_space = clipped.rfind(" ")
        title = f"{clipped[:last_space] if last_space > 0 else clipped}…"
    return title
