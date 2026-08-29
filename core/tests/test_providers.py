"""The provider seam: what every backend must offer, and what it may decline."""

from __future__ import annotations

import os

import pytest

from nexus_core.providers.base import Provider


async def test_chat_returns_text(provider):
    reply = await provider.chat([{"role": "user", "content": "hello"}])

    assert reply == "fake reply"
    assert provider.chat_calls == [[{"role": "user", "content": "hello"}]]


async def test_embed_returns_one_vector_per_input(provider):
    vectors = await provider.embed(["a", "b", "c"])

    assert len(vectors) == 3


async def test_speech_methods_raise_until_implemented(provider):
    """stt/tts are declared on the base class so the voice adapter has a home."""
    with pytest.raises(NotImplementedError):
        await provider.stt(b"")
    with pytest.raises(NotImplementedError):
        await provider.tts("hi")


def test_chat_and_embed_are_required_of_every_provider():
    """A backend cannot be instantiated without the two mandatory capabilities."""
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="costs money and needs network; set RUN_LIVE_TESTS=1 to run",
)
async def test_live_openai_completion():
    """Smoke test against the real API. Opt-in, never part of the default run."""
    from nexus_core.config import Settings
    from nexus_core.providers import build_provider

    real = build_provider(Settings())
    try:
        reply = await real.chat(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=16,
        )
        assert reply.strip()
    finally:
        await real.aclose()
