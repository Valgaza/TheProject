"""Configuration for the memory service.

Model names and the embedding dimension are read from the environment rather
than written into ``main.py``, matching ``core/app/config.py``. The dimension in
particular has to be explicit: graphiti's ``EmbedderConfig`` defaults it to
``int(os.getenv('EMBEDDING_DIM', 1024))`` at *import* time and then slices every
vector to that length, so leaving it alone silently truncated
``text-embedding-3-small`` from 1536 dimensions to 1024.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return value


def _optional(key: str, default: str) -> str:
    value = os.getenv(key)
    return value if value else default


class Settings:
    def __init__(self) -> None:
        self.neo4j_uri = _require("NEO4J_URI")
        self.neo4j_username = _require("NEO4J_USERNAME")
        self.neo4j_password = _require("NEO4J_PASSWORD")
        self.openai_api_key = _require("OPENAI_API_KEY")

        # The model graphiti uses to pull entities and relationships out of an
        # episode. Not the model that answers the user -- that one is core's.
        self.extraction_model = _optional(
            "MEMORY_EXTRACTION_MODEL", "gpt-5.4-nano-2026-03-17"
        )
        self.embedding_model = _optional("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")

        # Changing this invalidates every vector already stored, because
        # similarity is only meaningful between vectors of the same length.
        # Treat it as fixed once the graph has real content in it.
        self.embedding_dim = int(_optional("EMBEDDING_DIM", "1536"))
