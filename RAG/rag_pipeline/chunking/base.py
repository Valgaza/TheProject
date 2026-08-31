"""Abstract base class for chunking strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from rag_pipeline.models import Chunk, Document


class BaseChunker(ABC):
    """Base interface for text chunking strategies.

    Implementations split a Document into smaller Chunks suitable for
    embedding. Each chunk preserves its lineage to the parent document.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        """Initialize chunker with size constraints.

        Args:
            chunk_size: Maximum number of characters per chunk.
            chunk_overlap: Number of overlapping characters between consecutive chunks.
        """
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be less than chunk_size ({chunk_size})"
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @abstractmethod
    def chunk(self, document: Document) -> list[Chunk]:
        """Split a document into chunks.

        Args:
            document: The document to split.

        Returns:
            A list of Chunk objects with metadata linked to the parent document.
        """

    def chunk_batch(self, documents: list[Document]) -> list[Chunk]:
        """Chunk multiple documents, returning a flat list of all chunks.

        Args:
            documents: List of documents to chunk.

        Returns:
            Flat list of all chunks across all documents.
        """
        all_chunks: list[Chunk] = []
        for doc in documents:
            all_chunks.extend(self.chunk(doc))
        return all_chunks
