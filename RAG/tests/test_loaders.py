"""Tests for document loaders."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rag_pipeline.loaders.markdown_loader import MarkdownLoader
from rag_pipeline.loaders.registry import get_loader, get_supported_extensions
from rag_pipeline.models import DocumentType


class TestMarkdownLoader:
    """Tests for the Markdown loader."""

    def test_load_simple_markdown(self, sample_md_path: Path):
        """Test loading a standard markdown file."""
        loader = MarkdownLoader()
        docs = loader.load(sample_md_path)

        assert len(docs) == 1
        doc = docs[0]
        assert doc.content  # not empty
        assert doc.metadata.document_type == DocumentType.MARKDOWN
        assert doc.metadata.file_name == "sample.md"
        assert len(doc.metadata.headings) > 0

    def test_load_markdown_with_frontmatter(self, tmp_path: Path):
        """Test that YAML frontmatter is extracted."""
        md_file = tmp_path / "test.md"
        md_file.write_text(
            "---\ntitle: Test Doc\nauthor: Unit Test\n---\n\n# Hello\n\nContent here."
        )

        loader = MarkdownLoader()
        docs = loader.load(md_file)

        assert len(docs) == 1
        assert docs[0].metadata.extra.get("frontmatter", {}).get("title") == "Test Doc"

    def test_load_empty_markdown(self, tmp_path: Path):
        """Test that empty files return no documents."""
        md_file = tmp_path / "empty.md"
        md_file.write_text("---\ntitle: Empty\n---\n\n")

        loader = MarkdownLoader()
        docs = loader.load(md_file)
        assert len(docs) == 0

    def test_load_nonexistent_file(self):
        """Test that FileNotFoundError is raised for missing files."""
        loader = MarkdownLoader()
        with pytest.raises(FileNotFoundError):
            loader.load(Path("/nonexistent/file.md"))

    def test_load_wrong_extension(self, tmp_path: Path):
        """Test that ValueError is raised for wrong file types."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("hello")

        loader = MarkdownLoader()
        with pytest.raises(ValueError, match="Not a Markdown file"):
            loader.load(txt_file)


class TestLoaderRegistry:
    """Tests for the loader registry."""

    def test_get_loader_markdown(self, tmp_path: Path):
        """Test that markdown extensions resolve to MarkdownLoader."""
        loader = get_loader(tmp_path / "test.md")
        assert isinstance(loader, MarkdownLoader)

    def test_get_supported_extensions(self):
        """Test that supported extensions list is populated."""
        exts = get_supported_extensions()
        assert ".md" in exts
        assert ".pdf" in exts

    def test_unsupported_extension(self, tmp_path: Path):
        """Test that unsupported extensions raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported file extension"):
            get_loader(tmp_path / "test.docx")
