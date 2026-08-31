"""Heading-aware Markdown chunker that preserves document hierarchy."""

from __future__ import annotations

import logging
import re

from rag_pipeline.chunking.base import BaseChunker
from rag_pipeline.chunking.recursive_chunker import RecursiveChunker
from rag_pipeline.models import Chunk, ChunkMetadata, Document

logger = logging.getLogger(__name__)

# Matches Markdown headings: # H1, ## H2, ### H3, etc.
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class MarkdownChunker(BaseChunker):
    """Heading-aware chunker that splits Markdown by section structure.

    Splits at heading boundaries (# through ######), preserving the
    heading path as metadata (e.g., "Introduction > Background > History").

    Sections that exceed max chunk size are further split using the
    recursive chunker as a fallback.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        min_heading_level: int = 1,
        max_heading_level: int = 3,
    ):
        """Initialize the Markdown chunker.

        Args:
            chunk_size: Max characters per chunk.
            chunk_overlap: Overlap characters between chunks.
            min_heading_level: Minimum heading level to split on (1 = H1).
            max_heading_level: Maximum heading level to split on (3 = H3).
        """
        super().__init__(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.min_heading_level = min_heading_level
        self.max_heading_level = max_heading_level
        # Fallback for oversized sections
        self._recursive_chunker = RecursiveChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(self, document: Document) -> list[Chunk]:
        """Split a Markdown document by headings.

        Args:
            document: The Markdown document to chunk.

        Returns:
            List of Chunk objects with heading path metadata.
        """
        text = document.content.strip()
        if not text:
            return []

        # Parse sections by heading
        sections = self._split_by_headings(text)

        # Convert sections to chunks (splitting oversized ones)
        raw_chunks: list[tuple[str, str]] = []  # (heading_path, content)
        for heading_path, content in sections:
            content = content.strip()
            if not content:
                continue

            if len(content) <= self.chunk_size:
                raw_chunks.append((heading_path, content))
            else:
                # Section too large — recursively split it
                sub_doc = Document(
                    content=content,
                    metadata=document.metadata,
                )
                sub_chunks = self._recursive_chunker.chunk(sub_doc)
                for sc in sub_chunks:
                    raw_chunks.append((heading_path, sc.content))

        # Build final Chunk objects
        chunks: list[Chunk] = []
        for i, (heading_path, content) in enumerate(raw_chunks):
            meta = ChunkMetadata(
                document_id=document.id,
                source_path=document.metadata.source_path,
                document_type=document.metadata.document_type,
                file_name=document.metadata.file_name,
                page_number=document.metadata.page_number,
                chunk_index=i,
                total_chunks=len(raw_chunks),
                heading_path=heading_path,
                char_count=len(content),
                extra=document.metadata.extra,
            )
            chunks.append(Chunk(content=content, metadata=meta))

        logger.debug(
            "Markdown-chunked '%s' → %d chunks",
            document.metadata.file_name,
            len(chunks),
        )
        return chunks

    def _split_by_headings(self, text: str) -> list[tuple[str, str]]:
        """Split text into sections defined by Markdown headings.

        Returns:
            List of (heading_path, section_content) tuples.
        """
        # Find all heading positions
        headings: list[tuple[int, int, str]] = []  # (position, level, title)
        for match in _HEADING_PATTERN.finditer(text):
            level = len(match.group(1))
            if self.min_heading_level <= level <= self.max_heading_level:
                headings.append((match.start(), level, match.group(2).strip()))

        if not headings:
            # No headings found — return entire content as one section
            return [("", text)]

        # Build sections with heading paths
        sections: list[tuple[str, str]] = []

        # Content before first heading (preamble)
        if headings[0][0] > 0:
            preamble = text[:headings[0][0]].strip()
            if preamble:
                sections.append(("", preamble))

        # Track heading hierarchy for building paths
        heading_stack: list[tuple[int, str]] = []  # (level, title)

        for idx, (pos, level, title) in enumerate(headings):
            # Update heading stack — pop everything at the same or deeper level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))

            # Build heading path from stack
            heading_path = " > ".join(h[1] for h in heading_stack)

            # Extract section content (from this heading to next heading or end)
            start = pos
            end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
            section_content = text[start:end].strip()

            sections.append((heading_path, section_content))

        return sections
