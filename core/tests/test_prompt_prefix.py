"""The prompt keeps a stable prefix, so the runtime's cache carries a
conversation from turn to turn instead of prefilling it again."""

from __future__ import annotations

import uuid

from nexus_core import chat_service
from nexus_core.chat_service import CONTEXT_DIVIDER, _load_history, build_prompt
from nexus_core.memory_client import Fact
from nexus_core.models import Chat, Message
from nexus_core.rag_client import RagResult, RagSource

from conftest import DEMO_USER_ID


def _msg(role: str, content: str) -> Message:
    return Message(chat_id=uuid.uuid4(), role=role, content=content, meta={})


def test_what_changes_each_turn_sits_after_the_conversation_so_far():
    history = [_msg("user", "u1"), _msg("assistant", "a1"), _msg("user", "u2")]
    facts = [Fact(id="f", fact="Aarya uses Neo4j.", scope="user", kind="edge")]
    rag = RagResult(prompt="Section 3.", sources=(RagSource(file_name="h.pdf", rerank_score=0.9),))

    prompt = build_prompt(history, facts, rag)

    # Only a system message first, which every chat template accepts.
    assert [m["role"] for m in prompt] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in prompt[1:3]] == ["u1", "a1"]
    assert prompt[-1]["content"].endswith(f"{CONTEXT_DIVIDER}\nu2") and "Neo4j" in prompt[-1]["content"]
    # The next turn's prompt starts with exactly this one's first three messages.
    later = build_prompt(history + [_msg("assistant", "a2"), _msg("user", "u3")], facts, rag)
    assert later[:3] == prompt[:3]


def test_a_spoken_turn_asks_for_a_short_plain_answer():
    assert "spoken aloud" in build_prompt([_msg("user", "hi")], spoken=True)[0]["content"]
    assert "spoken aloud" not in build_prompt([_msg("user", "hi")])[0]["content"]


async def test_a_long_conversation_drops_old_turns_a_block_at_a_time(session_factory, monkeypatch):
    monkeypatch.setattr(chat_service, "HISTORY_LIMIT", 6)
    monkeypatch.setattr(chat_service, "HISTORY_STEP", 4)
    async with session_factory() as session:
        chat = Chat(user_id=DEMO_USER_ID, title="t")
        session.add(chat)
        await session.flush()
        firsts = []
        for i in range(12):
            session.add(Message(chat_id=chat.id, role="user", content=f"m{i}", meta={}))
            await session.flush()
            firsts.append((await _load_history(session, chat.id))[0].content)

    # Up to 6 messages nothing is dropped; past that the start moves 4 at a
    # time, so it changes on 2 turns out of 6 rather than on every one.
    assert firsts == ["m0"] * 6 + ["m4"] * 4 + ["m8"] * 2
