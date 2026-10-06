"""OpenAI-compatible provider.

Also serves any OpenAI-compatible server (Ollama, vLLM, LM Studio) by setting
``OPENAI_BASE_URL`` — which is the intended path to local inference later.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Sequence

from openai import AsyncOpenAI

from .base import Message, Provider

logger = logging.getLogger(__name__)

# The SDK's own default, used whenever we don't have an override. Duplicated
# here deliberately -- see the comment in __init__ for why we never pass a
# bare `None` through to `AsyncOpenAI(base_url=...)`.
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


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
        # Never pass a bare `None` for base_url -- when unset, AsyncOpenAI's
        # own __init__ falls back to reading os.environ["OPENAI_BASE_URL"]
        # itself rather than trusting our `None`. If that variable is merely
        # *present but blank* in the process environment (exactly what
        # python-dotenv's load_dotenv() does for `OPENAI_BASE_URL=` in
        # .env -- it sets the OS env var to "", not "unset"), the SDK's own
        # `if base_url is None` check never catches it, and it silently
        # builds every request with no host at all (httpx.UnsupportedProtocol
        # deep inside a retry loop, surfacing as a generic APIConnectionError
        # with no indication base_url was the cause). Resolving the default
        # ourselves sidesteps that env-var re-read entirely.
        resolved_base_url = base_url or _DEFAULT_OPENAI_BASE_URL

        # The SDK defaults to 600s with retries. A chat call is answered inline
        # in an HTTP request, so a hung upstream would hang the user's browser
        # for ten minutes; fail in one instead.
        self._client = AsyncOpenAI(api_key=api_key, base_url=resolved_base_url, timeout=timeout)
        self._chat_model = chat_model
        self._embed_model = embed_model

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
        resolved = model or self._chat_model
        kwargs: dict = {
            "model": resolved,
            "messages": list(messages),
            # Read by nexus_runtime's scheduler (runtime/nexus_runtime/engine.py)
            # so a live reply overtakes background work on the one GPU.
            "extra_headers": {"X-Nexus-Priority": priority},
        }
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        logger.info(
            "chat model=%s in=%d out=%d",
            resolved,
            response.usage.prompt_tokens if response.usage else -1,
            response.usage.completion_tokens if response.usage else -1,
        )
        return content

    async def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        priority: str = "interactive",
    ) -> AsyncIterator[str]:
        resolved = model or self._chat_model
        kwargs: dict = {
            "model": resolved,
            "messages": list(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_headers": {"X-Nexus-Priority": priority},
        }
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        stream = await self._client.chat.completions.create(**kwargs)
        usage = None
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            # Closing the stream is what tells the server to stop generating
            # when the reader went away early (a closed tab, an interruption).
            await stream.close()
        logger.info(
            "chat stream model=%s in=%d out=%d", resolved,
            usage.prompt_tokens if usage else -1, usage.completion_tokens if usage else -1,
        )

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
