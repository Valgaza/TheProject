"""OpenAI-compatible provider.

Also serves any OpenAI-compatible server (Ollama, vLLM, LM Studio) by setting
``OPENAI_BASE_URL`` — which is the intended path to local inference later.
"""

from __future__ import annotations

import logging
from typing import Sequence

from openai import AsyncOpenAI

from .base import Message, Provider

logger = logging.getLogger(__name__)


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        chat_model: str,
        embed_model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        # The SDK defaults to 600s with retries. A chat call is answered inline
        # in an HTTP request, so a hung upstream would hang the user's browser
        # for ten minutes; fail in one instead.
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._chat_model = chat_model
        self._embed_model = embed_model

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        resolved = model or self._chat_model
        kwargs: dict = {
            "model": resolved,
            "messages": list(messages),
        }
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens

        response = await self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        logger.info(
            "chat model=%s in=%d out=%d",
            resolved,
            response.usage.prompt_tokens if response.usage else -1,
            response.usage.completion_tokens if response.usage else -1,
        )
        return content

    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        resolved = model or self._embed_model
        response = await self._client.embeddings.create(
            model=resolved,
            input=list(texts),
        )
        # The API may return data out of order; index is authoritative.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    async def aclose(self) -> None:
        await self._client.close()
