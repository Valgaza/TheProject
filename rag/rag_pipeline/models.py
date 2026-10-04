"""Core data models for the RAG pipeline.

Every component communicates through these well-typed structures —
no raw dicts flowing between layers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DocumentType(str, Enum):
    """Supported document formats."""
    PDF = "pdf"
    MARKDOWN = "markdown"


class ChunkingStrategy(str, Enum):
    """Available chunking strategies."""
    RECURSIVE = "recursive"
    MARKDOWN = "markdown"


# ---------------------------------------------------------------------------
# Document & Chunk models
# ---------------------------------------------------------------------------

class DocumentMetadata(BaseModel):
    """Metadata attached to every loaded document."""
    source_path: str
    document_type: DocumentType
    file_name: str
    file_size_bytes: int = 0
    page_number: int | None = None  # PDF-specific
    total_pages: int | None = None  # PDF-specific
    headings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    """A loaded document (or page of a document) before chunking."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    metadata: DocumentMetadata


class ChunkMetadata(BaseModel):
    """Metadata for a single chunk, inheriting document-level info."""
    document_id: str
    source_path: str
    document_type: DocumentType
    file_name: str
    page_number: int | None = None
    chunk_index: int = 0
    total_chunks: int = 0
    heading_path: str = ""  # e.g. "Chapter 1 > Section 2"
    char_count: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A text chunk ready for embedding and storage."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    metadata: ChunkMetadata


# ---------------------------------------------------------------------------
# Embedding models
# ---------------------------------------------------------------------------

class EmbeddingResult(BaseModel):
    """Dense + sparse embedding pair for a single chunk."""
    chunk_id: str
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


# ---------------------------------------------------------------------------
# Search / Retrieval models
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    """A single search hit returned from the vector store."""
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankedResult(BaseModel):
    """A search result after cross-encoder reranking."""
    chunk_id: str
    content: str
    original_score: float
    rerank_score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline output models
# ---------------------------------------------------------------------------

class IngestionStats(BaseModel):
    """Statistics returned after ingestion completes."""
    documents_processed: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    points_upserted: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


class AssembledContext(BaseModel):
    """The final assembled context ready for LLM consumption."""
    context_text: str
    prompt: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    total_chunks_used: int = 0
    total_chars: int = 0


class QueryResult(BaseModel):
    """Complete result of a query through the full pipeline."""
    query: str
    assembled_context: AssembledContext
    retrieval_results: list[SearchResult] = Field(default_factory=list)
    reranked_results: list[RerankedResult] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
