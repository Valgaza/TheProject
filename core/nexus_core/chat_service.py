"""Turn a user message into an answer.

The whole generation path lives here so the router stays a thin HTTP shell and
so phase 4 has one obvious place to splice retrieval in: between building the
history and calling the provider.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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
from .web_client import WEB_TRIGGER, WebClient, WebSearch, accepts, offers_web
from .clock import calendar_note, now_note
from .images import ImageStore, StoredImage, caption, caption_note
from .decisions import WEB_INTENT_QUESTIONS, DecisionsClient, log_entry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Nexus, a local-first assistant. Answer directly and concretely. "
    "Prefer specifics over generalities, admit what you do not know, and keep "
    "formatting plain unless the answer genuinely needs structure. "
    # The web runs only when the person asks (flag 1), so the model is told
    # it exists and how to offer it, rather than claiming it can't.
    "Nexus can search the web, but only when the person asks, so you never "
    "browse on your own. Never say you have no way to reach the internet. "
    "When asked for a name, number or detail you weren't given and don't "
    "know, say you don't know rather than guessing. Never invent an amount, "
    "total, price or date; if asked about something you haven't been shown "
    "(a receipt, an image, a file), say you can't see it and ask for it. "
    "About the person's own life, work and plans, say only what you have been "
    "told (in memory or this conversation), including every relevant thing "
    "you were told; if it isn't there, reply \"I don't know\"."
)

# The offer to search is asked for only on a question that sounds
# time-sensitive. Told on every turn, the model ended nearly every answer with
# it, documents and the person's own facts included. It goes with the turn's
# context, not the system prompt, so the cached prefix stays the same. A bare
# year is not a cue ("my viva is on 3 November 2026" is the person's own date),
# and nor is "today": every turn carries the date, so "what's the date today?"
# needs no search.
RECENT_CUE = re.compile(
    r"\b(current(ly)?|latest|recent(ly)?|news|this (week|month|year)|"
    r"prices?|weather|scores?|won|winner|released?|president|prime minister|ceo|stocks?)\b",
    re.IGNORECASE,
)
OFFER_NOTE = (
    "This question may depend on recent or changing facts and no web results "
    "are attached. What you know stops well before today, so never state an "
    "event, result or figure from after that as if you knew it; say briefly what "
    "you last knew and that it may be out of date, or that you don't know, and "
    "end with exactly: \"Want me to check online?\" This is only about outside "
    "events: working things out from dates the person gives you, or from today's "
    "date, is fine, and needs no offer."
)

# Added for a spoken turn (voice/). Every word is waited for twice over, once
# generated and once said, and nothing on screen is read: so short, plain, and
# with the answer first, which is also the first sentence spoken.
SPOKEN_PROMPT = (
    " This conversation is spoken aloud. Answer in one to three short sentences, "
    "with the answer itself first. No lists, headings, markdown, URLs or source "
    "citations; say numbers and names the way a person would say them."
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


def render_memory(facts: Sequence[Fact], person: str | None = None) -> str:
    """Recalled facts as a system message.

    Framed as things already known rather than as instructions, and explicitly
    fallible. Presented as ground truth, a model will defend a stale fact
    against the user correcting it, which is exactly the failure that makes
    memory feel broken rather than helpful.
    """
    # Newest first, so a correction reads before what it corrected.
    ordered = sorted(facts, key=lambda f: f.learned_at.timestamp() if f.learned_at else float("-inf"),
                     reverse=True)
    lines = "\n".join(f"- {fact.render()}" for fact in ordered)
    return (
        "What you already know about this person and their work, recalled from "
        "earlier conversations, most recently learned first"
        + (f" (facts about {person} are about the person you're talking to)" if person else "")
        + ":\n"
        f"{lines}\n\n"
        "Use it where it is relevant and say nothing about it where it is not. "
        "When two of these disagree, the one listed first is current and the "
        "other is out of date: the person changed their mind. "
        "It may be incomplete or out of date; if the user contradicts it, they "
        "are right. If they ask about their own life, work or plans and none of "
        "these says it (other facts about them don't count), reply that you "
        "don't know (\"I don't know\"); never work it out or fill it in."
    )


_SOURCE_HEADER = re.compile(r"^\[Source: (?P<file>[^|\]]+?)(?: \| Chunk (?P<chunk>\d+))?\]\s*$", re.MULTILINE)


def document_passages(rag_result: RagResult) -> str:
    """Only the passages from the RAG service's assembled prompt, each
    labelled with its document and section.

    The assembled prompt is written for a model of its own: "You are a
    knowledgeable assistant... using ONLY the provided context... Cite the
    source", then the question again. Passed on whole, those instructions
    made answers say "the context provided does not mention", copy
    "[Source: ... | Chunk 4]" into the reply, and miss a name given only as
    the section heading ("## Pellinore" over "the dog died in 1919").
    """
    text = rag_result.prompt
    start, end = text.find("--- Retrieved Context ---"), text.find("--- End of Context ---")
    if start != -1:
        text = text[start + len("--- Retrieved Context ---"): end if end != -1 else None]
    sections = {str(src.chunk_index): src.heading_path for src in rag_result.sources}

    def label(m: re.Match) -> str:
        path = sections.get(m["chunk"] or "", "")
        return f"From {m['file'].strip()}" + (f", section \"{path}\":" if path else ":")

    return _SOURCE_HEADER.sub(label, text).strip()


def render_rag_context(rag_result: RagResult) -> str:
    """Retrieved document passages, presented as material to use, not as
    instructions, and explicitly allowed to be irrelevant."""
    return (
        "Passages retrieved from this person's documents, relevant to what "
        "was just asked. A section title is part of the text: a section "
        "titled with a name is about that name.\n\n"
        f"{document_passages(rag_result)}\n\n"
        "Use them where they answer the question, in your own words, without "
        "mentioning passages, chunks or sources (those are shown separately). "
        "If they don't answer it, say the documents don't say, rather than "
        "guessing."
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
        f"Web search results for \"{web.query_sent}\" (the query that was sent, "
        "with any names and identifying numbers already removed):\n\n"
        f"{lines}\n\n"
        "Answer from these results and cite them by number, e.g. [2]. Use only "
        "what they actually say: never state a name, date, winner or figure "
        "they don't contain. Where they disagree with anything said earlier in "
        "this conversation, the results are right and the earlier answer was "
        "wrong: say so. If they don't give the answer, say what they do show and "
        "that you couldn't find the rest. The search has already run, on a "
        "query with personal details removed: answer from the results, and "
        "don't say you can't search for or look up the person."
    )


def build_prompt(
    history: list[Message],
    facts: Sequence[Fact] = (),
    rag_result: RagResult | None = None,
    web: WebSearch | None = None,
    spoken: bool = False,
    now: datetime | None = None,
    images: Sequence[str] = (),
    person: str | None = None,
) -> list[ProviderMessage]:
    """The system prompt, the conversation so far, then the message being
    answered with what was recalled and retrieved for it.

    ``images`` are data URLs to show the vision model with the latest
    message. Images on earlier messages appear as their captions, which is
    all the chat model can take in (images.caption_note).

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
    system = SYSTEM_PROMPT + (SPOKEN_PROMPT if spoken else "")
    prompt: list[ProviderMessage] = [{"role": "system", "content": system}]
    context: list[str] = []
    turns = [m for m in history if m.role != "system"]
    latest = turns[-1] if turns and turns[-1].role == "user" else None
    if facts:
        context.append(render_memory(facts, person))
    elif latest_is_personal(history):
        context.append(NOTHING_RECALLED)
    if rag_result is not None and rag_result.prompt:
        context.append(render_rag_context(rag_result))
    if web is not None:
        context.append(render_web_context(web))

    recent = latest is not None and RECENT_CUE.search(latest.content)
    if latest is not None:
        # Every turn, first: without it the model takes its training cutoff
        # for the present (clock.py). Here, not in the system prompt, so the
        # cached prefix doesn't change every minute.
        context.insert(0, now_note(now))
        # And the counting done for it: weekdays and days-away for the dates
        # the recent conversation mentions, which the models get wrong alone.
        context.insert(1, calendar_note([_with_image_notes(m) for m in turns[-6:]], now))
        if person:
            context.insert(0, f"The person's name is {person}.")
    if recent and web is None and not (rag_result is not None and rag_result.prompt):
        context.append(OFFER_NOTE)
    if images:
        context.append(IMAGE_NOTE)
    for message in turns[:-1] if latest is not None else turns:
        prompt.append({"role": message.role, "content": _with_image_notes(message)})
    if latest is None:
        # Nothing to attach it to (not a real turn); keep it next to the
        # system prompt.
        prompt[1:1] = [{"role": "system", "content": block} for block in context]
        return prompt
    said = (latest.content.strip() or IMAGE_ONLY_PROMPT) if images else _with_image_notes(latest)
    text = "\n\n".join(context) + f"\n\n{CONTEXT_DIVIDER}\n{said}" if context else said
    if images:
        prompt.append({"role": "user", "content": [
            {"type": "text", "text": text},
            *({"type": "image_url", "image_url": {"url": url}} for url in images),
        ]})
    else:
        prompt.append({"role": "user", "content": text})
    return prompt


_NAME = re.compile(r"\b(?:my name is|my name's|call me|i'm called|i am called)\s+([A-Za-z][A-Za-z'-]{1,30})\b",
                   re.IGNORECASE)
_NOT_NAMES = {"not", "just", "actually", "also", "still", "the", "a", "an", "no"}


def stated_name(text: str) -> str | None:
    """The name in "my name is Aarya" / "call me Aarya", or None."""
    m = _NAME.search(text)
    if not m or m[1].lower() in _NOT_NAMES:
        return None
    return m[1][0].upper() + m[1][1:]


# A question about when something is, or how long until it: worth reading an
# image's dates before answering (prepare_turn).
TIME_QUESTION = re.compile(
    r"\b(when|how long|how many (days|weeks|months)|due|deadline|until|left|days?|weeks?|"
    r"dates?|expir\w*|overdue|time)\b", re.IGNORECASE)

# With images attached. Asked "what do these two show?" about a receipt and
# a sign, the vision model priced the receipt in rupees and decided the person
# was "visiting Kestrel Point today, and the receipt is from this visit".
IMAGE_NOTE = (
    "Images are attached. Say only what they show: read text and amounts "
    "exactly as printed, add no currency, place or date that isn't visible, "
    "and don't link images to each other or to the person unless they clearly do."
)

# A question about the person with nothing recalled for it: said outright,
# next to the question, because the system prompt's "say you don't know" alone
# didn't stop "What is my thesis about?" being answered with an invented one.
NOTHING_RECALLED = (
    "Nothing in memory matches this. If it asks about the person's own life, "
    "work or plans, reply \"I don't know\" rather than making anything up."
)
_PERSONAL = re.compile(r"\b(i|i'm|me|my|mine|we|our|us)\b", re.IGNORECASE)


def latest_is_personal(history) -> bool:
    """About the person or their own work, so only memory can answer it."""
    turns = [m for m in history if m.role != "system"]
    if not turns or turns[-1].role != "user":
        return False
    text = turns[-1].content
    return bool(_PERSONAL.search(text))


# What an image sent with no text is taken to ask.
IMAGE_ONLY_PROMPT = "What's in this image?"


def _with_image_notes(message: Message) -> str:
    notes = caption_note(getattr(message, "meta", None) or {})
    if not notes:
        return message.content
    return f"{message.content}\n{notes}".strip()


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
    # The model that answers: None for the chat model, the vision model's id
    # when the turn has images.
    model: str | None = None


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
    context: list[str] = (),
) -> WebSearch:
    # Only when asked for, and only through the websearch service, which
    # redacts: what goes out is its rewrite, never the raw message.
    if web is None:
        return WebSearch(query_sent=None, error="web search is not configured")
    try:
        return await web.search(content, known_names=known_names, user_id=str(user_id), context=context)
    except Exception:
        # WebClient.search() already turns its own failures into an error;
        # this catches anything else, same as memory and RAG.
        logger.exception("Web search raised for chat=%s", chat.id)
        return WebSearch(query_sent=None, error="web search failed")


def _earlier_user_messages(history: list[Message], limit: int = 3) -> list[str]:
    """What the person said before this message: "check it online" means
    nothing to the search rewrite without the question it follows. Their own
    words only, read by the local rewrite; nothing here is sent as is."""
    said = [m.content for m in history if m.role == "user"]
    return said[:-1][-limit:]


async def _images_just_shown(
    session: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID, store: ImageStore,
) -> list[StoredImage]:
    """The images on the previous message, if the last exchange was about
    images: a question straight after one is usually about them too. Only one
    turn on; after that the image is known by its caption."""
    rows = (await session.execute(
        select(Message).where(Message.chat_id == chat_id, Message.role != "system")
        .order_by(Message.created_at.desc()).limit(2)
    )).scalars().all()
    if len(rows) != 2 or rows[0].role != "assistant" or rows[1].role != "user":
        return []
    found = (store.get(user_id, uuid.UUID(img["id"])) for img in (rows[1].meta or {}).get("images", []))
    return [img for img in found if img is not None]


async def question_offered_a_search(session: AsyncSession, chat_id: uuid.UUID) -> str | None:
    """If the chat's last answer offered to check online, the question it
    was answering; otherwise None."""
    rows = (await session.execute(
        select(Message).where(Message.chat_id == chat_id, Message.role != "system")
        .order_by(Message.created_at.desc()).limit(2)
    )).scalars().all()
    if len(rows) == 2 and rows[0].role == "assistant" and rows[1].role == "user" \
            and offers_web(rows[0].content):
        return rows[1].content
    return None


@dataclass
class Retrieval:
    """Memory and documents, fetched for a message before its turn begins.

    Voice starts this during the speaker's pause, on a speculative
    transcript, so by the time the pause is long enough to end the turn the
    retrieval is done. prepare_turn uses it only if the message it is
    preparing is the same text.
    """

    content: str
    facts: asyncio.Task | None
    documents: asyncio.Task | None
    timings: dict[str, int] = field(default_factory=dict)

    def discard(self) -> None:
        for task in (self.facts, self.documents):
            if task is not None:
                task.cancel()


def start_retrieval(
    *, content: str, chat: Chat, user_id: uuid.UUID, memory: MemoryClient | None,
    rag: RagClient | None, rag_min_score: float = 0.5,
) -> Retrieval:
    timings: dict[str, int] = {}
    return Retrieval(
        content=content,
        facts=asyncio.create_task(_timed(timings, "memory_ms", _search_memory(memory, content, user_id, chat)))
        if memory is not None else None,
        documents=asyncio.create_task(_timed(
            timings, "rag_ms", _query_documents(rag, content, user_id, chat, rag_min_score, timings)))
        if rag is not None else None,
        timings=timings,
    )


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
    spoken: bool = False,
    retrieval: Retrieval | None = None,
    images: Sequence[StoredImage] = (),
    image_store: ImageStore | None = None,
    vision_model: str | None = None,
) -> PreparedTurn:
    """Store the user message and gather everything the answer needs.

    ``retrieval``, if given and for this same text, supplies memory and
    documents already fetched (see Retrieval); otherwise they are fetched here.

    ``images`` (already checked to be this person's) make it a vision turn:
    the vision model answers, looking at them. So does a message straight
    after one ("what does the sign say?"), which sees the same images again.
    """
    turn_started = time.perf_counter()
    images = list(images)
    carried = False
    if not images and image_store is not None and vision_model:
        images = await _images_just_shown(session, chat.id, user_id, image_store)
        carried = bool(images)
    # Wall-clock per stage, in ms, stored on the answer as meta.timings. Every
    # latency change in the milestone is judged against these numbers.
    timings: dict[str, int] = {}

    # "Yes" to Nexus's own "Want me to check online?": the search is asked
    # for, and it is for the question that prompted the offer.
    offered_question = None
    if not use_web and not WEB_TRIGGER.search(content) and accepts(content):
        offered_question = await question_offered_a_search(session, chat.id)

    # Only asked when nothing cheaper has already decided: the button and the
    # trigger phrases settle most web requests without a model.
    intent = None
    ask_laya = (
        decisions is not None and laya_web_intent in ("shadow", "live")
        and not use_web and not WEB_TRIGGER.search(content) and offered_question is None
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

    attachments = (*attachments, *({"kind": "image", "uri": f"/images/{img.id}", "mime_type": img.mime_type,
                                    "name": img.name} for img in images))
    route = await select_route(
        content=content, attachments=attachments, rag=rag, use_web=use_web,
        accepted_web_offer=offered_question is not None,
        web_intent_p=intent.p("asks_for_web") if intent and laya_web_intent == "live" else None,
        web_intent_above=laya_web_intent_above,
    )

    if carried and route.handler == "vision":
        route = Route(handler="vision", reason="a follow-up about the image just shown")
    user_meta: dict = {"route": route.handler}
    if images and not carried:
        user_meta["images"] = [img.meta() for img in images]
        if TIME_QUESTION.search(content) and image_store is not None and vision_model:
            # A question about when, in an image: read its text first, so the
            # dates in it are counted here (clock.calendar_note) rather than
            # by the vision model, which miscounts (39, 49, 50 and "10" days
            # for a 40-day wait). The caption is the one written after the
            # answer anyway, just earlier.
            stage = time.perf_counter()
            for meta, img in zip(user_meta["images"], images):
                try:
                    meta["caption"] = await caption(provider, vision_model,
                                                    await asyncio.to_thread(image_store.data_url, user_id, img))
                except Exception:
                    logger.exception("reading an image ahead of its answer failed")
            timings["image_read_ms"] = _ms_since(stage)
    user_message = Message(chat_id=chat.id, role="user", content=content, meta=user_meta)
    profile = await session.get(Profile, user_id)
    if profile is not None and (stated := stated_name(content)) and stated != profile.display_name:
        # "My name is Aarya": the account takes the name, so memory writes
        # about Aarya from here on, not about whatever the sign-up said.
        logger.info("display name changed by the person: %r -> %r", profile.display_name, stated)
        profile.display_name = stated
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
        asyncio.create_task(chat_title.generate(
            provider, user_message=(content.strip() or f"An image: {images[0].name}") if images else content,
            model=title_model))
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

    if retrieval is not None and retrieval.content != content:
        retrieval.discard()
        retrieval = None
    if retrieval is None:
        # An image sent with no words has nothing to look up memory or
        # documents with.
        said = bool(content.strip())
        retrieval = start_retrieval(content=content, chat=chat, user_id=user_id,
                                    memory=memory if said else None, rag=rag if said else None,
                                    rag_min_score=rag_min_score)
    else:
        timings["prefetched"] = 1

    async def done(task):
        return await task if task is not None else None

    stage = time.perf_counter()
    facts, rag_result, web_search = await asyncio.gather(
        done(retrieval.facts),
        done(retrieval.documents),
        _timed(timings, "web_ms", _search_web(web, offered_question or content, known_names, user_id, chat,
                                              context=_earlier_user_messages(history)))
        if route.handler == "web" else nothing(),
    )
    timings.update(retrieval.timings)
    if "prefetched" in timings:
        # What was left to wait for: the rest ran during the pause.
        timings["retrieval_wait_ms"] = _ms_since(stage)
    facts = facts or []
    if shadow_intent is not None:
        log_intent(await shadow_intent)

    # The route recorded is what actually happened, not what was predicted
    # before retrieval ran. "rag" means documents genuinely informed this
    # answer -- they cleared the floor -- which is the only version of the
    # label a person can act on. A web turn keeps its label even when
    # documents also helped: it is the one route that says words left the
    # machine.
    if rag_result is not None and route.handler not in ("web", "vision"):
        n = len(rag_result.sources)
        route = Route(
            handler="rag",
            reason=f"{n} document {'chunk' if n == 1 else 'chunks'} cleared the relevance floor "
                   f"(best {rag_result.best_score:.2f})",
        )

    urls = [await asyncio.to_thread(image_store.data_url, user_id, img) for img in images] \
        if images and image_store is not None else []
    prompt = build_prompt(history, facts, rag_result, web_search, spoken=spoken, images=urls,
                          person=getattr(profile, "display_name", None))
    return PreparedTurn(
        chat=chat, content=content, user_id=user_id, route=route, user_message=user_message,
        history=history, facts=facts, rag_result=rag_result, web_search=web_search,
        prompt=prompt, title_task=title_task, timings=timings, started=turn_started,
        model=vision_model if urls else None,
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
    extra_meta: dict | None = None,
) -> Turn:
    """Store the answer with everything that explains it, and finish the title.

    ``extra_meta`` is merged into the stored meta: a spoken turn adds
    ``voice`` (its own timings) this way.
    """
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
    if extra_meta:
        meta.update(extra_meta)

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
        answer = await provider.chat(prepared.prompt, model=prepared.model, priority="live")
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
        async for piece in provider.chat_stream(prepared.prompt, model=prepared.model, priority="live"):
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
