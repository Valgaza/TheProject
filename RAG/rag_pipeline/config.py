"""Centralized configuration using pydantic-settings with .env support."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """RAG pipeline configuration.

    Values are loaded from environment variables or a .env file.
    All settings have sensible defaults for local development.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Qdrant ---
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_name: str = "rag_documents"
    qdrant_use_memory: bool = False  # True = in-memory (no Docker needed)

    # --- Embedding models (FastEmbed / ONNX) ---
    dense_embedding_model: str = "BAAI/bge-small-en-v1.5"
    sparse_embedding_model: str = "Qdrant/bm25"
    embedding_batch_size: int = 64

    # --- Chunking ---
    chunk_size: int = 512
    chunk_overlap: int = 50

    # --- Retrieval ---
    retrieval_top_k: int = 20  # candidates from vector search
    rerank_top_n: int = 5  # final results after reranking
    rerank_score_threshold: float = 0.01  # minimum cross-encoder score

    # --- Reranker ---
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-12-v2"

    # --- Context assembly ---
    max_context_tokens: int = 3000  # rough char-based limit (≈ tokens * 4)

    @property
    def max_context_chars(self) -> int:
        """Approximate character limit derived from token budget."""
        return self.max_context_tokens * 4

    @property
    def env_file_path(self) -> Path:
        """Resolved path to the .env file."""
        return Path(self.model_config.get("env_file", ".env"))


# Singleton — import and use directly
settings = Settings()
