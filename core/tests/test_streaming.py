"""The streamed answer: sources first, then the words, then the stored turn."""

from __future__ import annotations

import json

from nexus_core.rag_client import RagResult, RagSource


class StreamingProvider:
    """Speaks in pieces, the way the runtime streams."""

    name = "streaming"

    def __init__(self, pieces=("Hel", "lo, ", "world.")) -> None:
        self.pieces = pieces
        self.priorities: list[str] = []

    async def chat(self, messages, *, model=None, max_tokens=None, priority="interactive",
                   response_format=None):
        return "A Title"

    async def chat_stream(self, messages, *, model=None, max_tokens=None, priority="interactive"):
        self.priorities.append(priority)
        for piece in self.pieces:
            yield piece

    async def embed(self, texts, *, model=None):
        return [[0.0] for _ in texts]


class FakeRag:
    async def query(self, question, *, user_id, project_id):
        return RagResult(prompt="Section 3: remote work is permitted.",
                         sources=(RagSource(file_name="handbook.pdf", rerank_score=0.9),))


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def _chat(db_client) -> dict:
    return (await db_client.post("/chats", json={})).json()


async def test_a_streamed_turn_sends_prepared_tokens_then_done(db_client):
    provider = StreamingProvider()
    db_client._transport.app.state.provider = provider
    db_client._transport.app.state.rag = FakeRag()
    chat = await _chat(db_client)

    response = await db_client.post(f"/chats/{chat['id']}/messages/stream",
                                    json={"content": "can I work remote?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)

    kinds = [k for k, _ in events]
    assert kinds[0] == "prepared" and kinds[-1] == "done"
    assert kinds[1:-1] == ["token"] * 3
    prepared = events[0][1]
    assert prepared["route"]["handler"] == "rag"
    assert prepared["rag_sources"][0]["file_name"] == "handbook.pdf"
    assert "".join(d["text"] for k, d in events if k == "token") == "Hello, world."
    done = events[-1][1]
    assert done["assistant_message"]["content"] == "Hello, world."
    timings = done["assistant_message"]["metadata"]["timings"]
    assert timings["first_token_ms"] <= timings["total_ms"]
    assert provider.priorities == ["live"]


async def test_the_streamed_answer_is_stored(db_client):
    db_client._transport.app.state.provider = StreamingProvider()
    chat = await _chat(db_client)
    await db_client.post(f"/chats/{chat['id']}/messages/stream", json={"content": "hi there"})

    messages = (await db_client.get(f"/chats/{chat['id']}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Hello, world."


async def test_a_provider_without_streaming_still_streams_one_piece(db_client, provider):
    """The base Provider buffers: the stream is one token, the whole reply."""
    chat = await _chat(db_client)
    response = await db_client.post(f"/chats/{chat['id']}/messages/stream", json={"content": "hi there"})
    tokens = [d["text"] for k, d in _events(response.text) if k == "token"]
    assert tokens == ["fake reply"]


async def test_streaming_to_a_missing_chat_is_a_404(db_client):
    response = await db_client.post(
        "/chats/00000000-0000-0000-0000-00000000dead/messages/stream", json={"content": "hi"}
    )
    assert response.status_code == 404


async def test_a_failure_mid_stream_still_stores_the_turn(db_client):
    class Broken(StreamingProvider):
        async def chat_stream(self, messages, **kwargs):
            yield "partial "
            raise RuntimeError("runtime went away")

    db_client._transport.app.state.provider = Broken()
    chat = await _chat(db_client)
    events = _events((await db_client.post(
        f"/chats/{chat['id']}/messages/stream", json={"content": "hi there"})).text)

    done = events[-1][1]
    assert events[-1][0] == "done"
    assert done["assistant_message"]["content"] == "partial "
    assert "runtime went away" in done["assistant_message"]["metadata"]["error"]
