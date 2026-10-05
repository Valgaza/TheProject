"""The single seam between this service and any model.

Nothing in ``core`` may import ``openai`` (or any other vendor SDK) outside of a
provider implementation. That rule is what keeps the eventual move to local
inference a config change: swap the provider, or point the OpenAI-compatible one
at a local base URL, and the rest of the service does not notice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Literal, Sequence, TypedDict

Role = Literal["system", "user", "assistant"]


class Message(TypedDict):
    """One turn in a conversation, in the shape every chat API expects."""

    role: Role
    content: str


class Provider(ABC):
    """Capabilities a model backend can offer.

    ``chat`` and ``embed`` are required. ``stt`` and ``tts`` are declared here so
    the voice adapter has somewhere to land, and raise until a provider
    implements them rather than being invented at that point.
    """

    name: str = "provider"

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        priority: str = "interactive",
        response_format: dict | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return the assistant's reply to ``messages``.

        ``response_format`` is passed through as OpenAI defines it. With a
        ``json_schema`` the local runtime constrains decoding to the schema,
        so the reply always parses.

        ``priority`` tells a local runtime how urgent this call is: "live"
        for a reply someone is waiting on, "interactive" for small side calls
        (titles, the write gate), "bulk" for background work. Servers that do
        not know the header ignore it.

        ``temperature`` 0 for decisions that should not depend on the dice
        (the write gate); None leaves the server's default.
        """

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        priority: str = "interactive",
    ) -> AsyncIterator[str]:
        """The reply to ``messages``, a piece at a time as it is generated.

        The default buffers: one piece, the whole reply. Providers that can
        stream override it, and callers never need to know which they got.
        """
        yield await self.chat(messages, model=model, max_tokens=max_tokens, priority=priority)

    @abstractmethod
    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """Return one embedding vector per input string, in order."""

    async def stt(self, audio: bytes, *, model: str | None = None) -> str:
        """Transcribe speech. Implemented when the voice adapter is built."""
        raise NotImplementedError(f"{self.name} does not implement speech-to-text")

    async def tts(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str | None = None,
    ) -> bytes:
        """Synthesize speech. Implemented when the voice adapter is built."""
        raise NotImplementedError(f"{self.name} does not implement text-to-speech")
