"""Markdown document loader with optional frontmatter extraction."""

from __future__ import annotations

import logging
from pathlib import Path

from rag_pipeline.loaders.base import DocumentLoader
from rag_pipeline.models import Document, DocumentMetadata, DocumentType

logger = logging.getLogger(__name__)


class MarkdownLoader(DocumentLoader):
    """Load Markdown files, extracting frontmatter metadata if present.

    Supports standard Markdown files with optional YAML frontmatter
    (delimited by --- at the top of the file).
    """

    def load(self, file_path: Path) -> list[Document]:
        """Load a Markdown file and return a single Document.

        Args:
            file_path: Path to the .md file.

        Returns:
            List containing a single Document (or empty if file has no content).
        """
        import frontmatter

        resolved = self._validate_path(file_path)
        if resolved.suffix.lower() not in (".md", ".markdown", ".mdx"):
            raise ValueError(f"Not a Markdown file: {resolved}")

        file_size = resolved.stat().st_size
        logger.info("Loading Markdown: %s (%.1f KB)", resolved.name, file_size / 1024)

        raw_text = resolved.read_text(encoding="utf-8")

        # Parse frontmatter (if any)
        post = frontmatter.loads(raw_text)
        content = post.content.strip()
        front_meta = dict(post.metadata) if post.metadata else {}

        if not content:
            logger.warning("Markdown file is empty: %s", resolved.name)
            return []

        # Extract headings for metadata
        headings = [
            line.lstrip("#").strip()
            for line in content.split("\n")
            if line.startswith("#")
        ]

        metadata = DocumentMetadata(
            source_path=str(resolved),
            document_type=DocumentType.MARKDOWN,
            file_name=resolved.name,
            file_size_bytes=file_size,
            headings=headings,
            extra={"frontmatter": front_meta} if front_meta else {},
        )

        logger.info(
            "Loaded Markdown: %s (%d chars, %d headings)",
            resolved.name, len(content), len(headings),
        )
        return [Document(content=content, metadata=metadata)]
