"""Recursive character text splitter with configurable separators and overlap."""

from __future__ import annotations

import logging

from rag_pipeline.chunking.base import BaseChunker
from rag_pipeline.models import Chunk, ChunkMetadata, Document

logger = logging.getLogger(__name__)

# Separator hierarchy: split by the most meaningful boundary first,
# falling back to progressively smaller boundaries.
DEFAULT_SEPARATORS = [
    "\n\n",   # Paragraph breaks
    "\n",     # Line breaks
    ". ",     # Sentence endings
    "? ",     # Question endings
    "! ",     # Exclamation endings
    "; ",     # Semicolons
    ", ",     # Commas
    " ",      # Word boundaries
    "",       # Character-level (last resort)
]


class RecursiveChunker(BaseChunker):
    """Recursive text splitter that respects natural text boundaries.

    Splits text using a hierarchy of separators, starting with paragraph
    breaks and falling back to smaller boundaries if chunks exceed the
    maximum size. This preserves semantic coherence within each chunk.

    The overlap parameter ensures that context at chunk boundaries is
    not lost during retrieval.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        separators: list[str] | None = None,
    ):
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.separators = separators or DEFAULT_SEPARATORS

    def chunk(self, document: Document) -> list[Chunk]:
        """Split a document recursively using the separator hierarchy.

        Args:
            document: The document to chunk.

        Returns:
            List of Chunk objects with proper metadata.
        """
        text = document.content.strip()
        if not text:
            return []

        raw_chunks = self._split_text(text, self.separators)

        # Build Chunk objects with metadata
        chunks: list[Chunk] = []
        for i, chunk_text in enumerate(raw_chunks):
            meta = ChunkMetadata(
                document_id=document.id,
                source_path=document.metadata.source_path,
                document_type=document.metadata.document_type,
                file_name=document.metadata.file_name,
                page_number=document.metadata.page_number,
                chunk_index=i,
                total_chunks=len(raw_chunks),
                char_count=len(chunk_text),
                extra=document.metadata.extra,
            )
            chunks.append(Chunk(content=chunk_text, metadata=meta))

        logger.debug(
            "Recursively chunked document '%s' → %d chunks (avg %d chars)",
            document.metadata.file_name,
            len(chunks),
            sum(c.metadata.char_count for c in chunks) // max(len(chunks), 1),
        )
        return chunks

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using the separator hierarchy."""
        final_chunks: list[str] = []

        # Find the best separator for this text
        separator = separators[-1]  # fallback
        for sep in separators:
            if sep == "":
                separator = sep
                break
            if sep in text:
                separator = sep
                break

        # Split the text
        splits = text.split(separator) if separator else list(text)

        # Merge smaller pieces back together up to chunk_size
        current_pieces: list[str] = []
        current_len = 0

        for piece in splits:
            piece_len = len(piece) + (len(separator) if current_pieces else 0)

            if current_len + piece_len > self.chunk_size and current_pieces:
                # Flush current buffer
                merged = separator.join(current_pieces)
                if len(merged) > self.chunk_size:
                    # Still too big — recurse with next separator level
                    remaining_seps = separators[separators.index(separator) + 1:] if separator in separators else separators[1:]
                    if remaining_seps:
                        final_chunks.extend(self._split_text(merged, remaining_seps))
                    else:
                        final_chunks.append(merged)
                else:
                    final_chunks.append(merged)

                # Start new buffer with overlap from previous chunk
                overlap_pieces = self._get_overlap_pieces(current_pieces, separator)
                current_pieces = overlap_pieces
                current_len = sum(len(p) for p in current_pieces) + len(separator) * max(len(current_pieces) - 1, 0)

            current_pieces.append(piece)
            current_len += piece_len

        # Flush remaining
        if current_pieces:
            merged = separator.join(current_pieces)
            if len(merged) > self.chunk_size:
                remaining_seps = separators[separators.index(separator) + 1:] if separator in separators else separators[1:]
                if remaining_seps:
                    final_chunks.extend(self._split_text(merged, remaining_seps))
                else:
                    final_chunks.append(merged)
            else:
                final_chunks.append(merged)

        # Filter out empty chunks and strip whitespace
        return [c.strip() for c in final_chunks if c.strip()]

    def _get_overlap_pieces(self, pieces: list[str], separator: str) -> list[str]:
        """Get pieces from the end of the buffer for overlap."""
        if self.chunk_overlap <= 0:
            return []

        overlap_pieces: list[str] = []
        overlap_len = 0
        for piece in reversed(pieces):
            new_len = overlap_len + len(piece) + (len(separator) if overlap_pieces else 0)
            if new_len > self.chunk_overlap:
                break
            overlap_pieces.insert(0, piece)
            overlap_len = new_len

        return overlap_pieces
