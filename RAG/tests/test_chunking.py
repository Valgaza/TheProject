"""Tests for chunking strategies."""

from __future__ import annotations

import pytest

from rag_pipeline.chunking.markdown_chunker import MarkdownChunker
from rag_pipeline.chunking.recursive_chunker import RecursiveChunker
from rag_pipeline.models import Document, DocumentMetadata, DocumentType


def _make_document(content: str, file_name: str = "test.md") -> Document:
    """Helper to create a Document for testing."""
    return Document(
        content=content,
        metadata=DocumentMetadata(
            source_path=f"/test/{file_name}",
            document_type=DocumentType.MARKDOWN,
            file_name=file_name,
            file_size_bytes=len(content),
        ),
    )


class TestRecursiveChunker:
    """Tests for the recursive text splitter."""

    def test_short_text_single_chunk(self, sample_text: str):
        """Text shorter than chunk_size should produce one chunk."""
        chunker = RecursiveChunker(chunk_size=1000, chunk_overlap=50)
        doc = _make_document(sample_text)
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert chunks[0].content == sample_text
        assert chunks[0].metadata.chunk_index == 0
        assert chunks[0].metadata.total_chunks == 1

    def test_long_text_multiple_chunks(self, long_sample_text: str):
        """Long text should be split into multiple chunks."""
        chunker = RecursiveChunker(chunk_size=200, chunk_overlap=30)
        doc = _make_document(long_sample_text)
        chunks = chunker.chunk(doc)

        assert len(chunks) > 1
        for chunk in chunks:
            # Each chunk should be at or under the size limit
            # (some may exceed slightly due to splitting boundaries)
            assert len(chunk.content) <= 400  # generous margin

    def test_chunk_metadata_preserved(self, sample_text: str):
        """Chunk metadata should link back to the parent document."""
        chunker = RecursiveChunker(chunk_size=1000)
        doc = _make_document(sample_text, "report.md")
        chunks = chunker.chunk(doc)

        assert chunks[0].metadata.document_id == doc.id
        assert chunks[0].metadata.file_name == "report.md"
        assert chunks[0].metadata.document_type == DocumentType.MARKDOWN

    def test_empty_document(self):
        """Empty documents should produce no chunks."""
        chunker = RecursiveChunker()
        doc = _make_document("")
        chunks = chunker.chunk(doc)
        assert len(chunks) == 0

    def test_invalid_overlap(self):
        """Overlap >= chunk_size should raise ValueError."""
        with pytest.raises(ValueError, match="chunk_overlap"):
            RecursiveChunker(chunk_size=100, chunk_overlap=100)

    def test_chunk_batch(self, sample_text: str):
        """chunk_batch should process multiple documents."""
        chunker = RecursiveChunker(chunk_size=1000)
        docs = [_make_document(sample_text, f"doc{i}.md") for i in range(3)]
        chunks = chunker.chunk_batch(docs)
        assert len(chunks) == 3


class TestMarkdownChunker:
    """Tests for the heading-aware Markdown chunker."""

    def test_split_by_headings(self):
        """Markdown should be split at heading boundaries."""
        content = "# Chapter 1\n\nContent of chapter 1.\n\n# Chapter 2\n\nContent of chapter 2."
        chunker = MarkdownChunker(chunk_size=500)
        doc = _make_document(content)
        chunks = chunker.chunk(doc)

        assert len(chunks) == 2
        assert "Chapter 1" in chunks[0].content
        assert "Chapter 2" in chunks[1].content

    def test_heading_path_metadata(self):
        """Heading hierarchy should be recorded in metadata."""
        content = "# Main\n\nIntro.\n\n## Sub\n\nDetails."
        chunker = MarkdownChunker(chunk_size=500)
        doc = _make_document(content)
        chunks = chunker.chunk(doc)

        # Find the chunk with the sub-heading
        sub_chunks = [c for c in chunks if "Sub" in c.metadata.heading_path]
        assert len(sub_chunks) > 0
        assert "Main > Sub" in sub_chunks[0].metadata.heading_path

    def test_oversized_section_falls_back(self):
        """Sections exceeding chunk_size should be recursively split."""
        large_content = "# Title\n\n" + "A" * 1000
        chunker = MarkdownChunker(chunk_size=200, chunk_overlap=20)
        doc = _make_document(large_content)
        chunks = chunker.chunk(doc)

        assert len(chunks) > 1

    def test_no_headings(self):
        """Document with no headings should return as single chunk."""
        content = "Just plain text without any headings."
        chunker = MarkdownChunker(chunk_size=500)
        doc = _make_document(content)
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
