"""PDF document loader using pymupdf4llm for layout-aware extraction."""

from __future__ import annotations

import logging
from pathlib import Path

from rag_pipeline.loaders.base import DocumentLoader
from rag_pipeline.models import Document, DocumentMetadata, DocumentType

logger = logging.getLogger(__name__)


class PDFLoader(DocumentLoader):
    """Load PDF files using pymupdf4llm's layout-aware Markdown extraction.

    Features:
        - Converts each page to structured Markdown (preserving tables, headings, lists)
        - Handles multi-column layouts and reading order
        - Preserves page-level metadata for precise source attribution
        - Extracts one Document per page for granular chunking control
    """

    def load(self, file_path: Path) -> list[Document]:
        """Load a PDF and return one Document per page.

        Args:
            file_path: Path to the PDF file.

        Returns:
            List of Document objects, one per page with Markdown content.
        """
        import pymupdf4llm

        resolved = self._validate_path(file_path)
        if resolved.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {resolved}")

        file_size = resolved.stat().st_size
        logger.info("Loading PDF: %s (%.1f KB)", resolved.name, file_size / 1024)

        # Extract as a list of page dicts with metadata
        page_chunks = pymupdf4llm.to_markdown(
            str(resolved),
            page_chunks=True,  # returns list[dict] — one per page
        )

        documents: list[Document] = []
        total_pages = len(page_chunks)

        for page_data in page_chunks:
            # pymupdf4llm page_chunks returns dicts with 'metadata' and 'text' keys
            page_meta = page_data.get("metadata", {})
            page_text = page_data.get("text", "").strip()

            if not page_text:
                logger.debug("Skipping empty page %d", page_meta.get("page", 0))
                continue

            # Extract headings from the page text (lines starting with #)
            headings = [
                line.lstrip("#").strip()
                for line in page_text.split("\n")
                if line.startswith("#")
            ]

            page_number = page_meta.get("page", 0) + 1  # 0-indexed → 1-indexed

            metadata = DocumentMetadata(
                source_path=str(resolved),
                document_type=DocumentType.PDF,
                file_name=resolved.name,
                file_size_bytes=file_size,
                page_number=page_number,
                total_pages=total_pages,
                headings=headings,
                extra={
                    "pdf_metadata": {
                        k: v for k, v in page_meta.items()
                        if k != "page" and isinstance(v, (str, int, float, bool))
                    }
                },
            )

            documents.append(Document(content=page_text, metadata=metadata))

        logger.info(
            "Loaded %d pages from %s (%d non-empty)",
            total_pages, resolved.name, len(documents),
        )
        return documents
