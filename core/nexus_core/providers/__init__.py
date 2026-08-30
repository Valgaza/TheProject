"""Provider registry.

``build_provider`` is the only place a concrete backend is chosen. Everything
else asks for a ``Provider``.
"""

from __future__ import annotations

from ..config import Settings
from .base import Message, Provider, Role
from .openai_provider import OpenAIProvider

__all__ = ["Message", "Provider", "Role", "OpenAIProvider", "build_provider"]


def build_provider(settings: Settings) -> Provider:
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        chat_model=settings.chat_model,
        embed_model=settings.embed_model,
        base_url=settings.openai_base_url,
    )
