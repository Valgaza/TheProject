"""Loader registry — auto-selects the right loader based on file extension."""

from __future__ import annotations

import logging
from pathlib import Path

from rag_pipeline.loaders.base import DocumentLoader
from rag_pipeline.loaders.markdown_loader import MarkdownLoader
from rag_pipeline.loaders.pdf_loader import PDFLoader

logger = logging.getLogger(__name__)

# Extension → Loader class mapping
_LOADER_REGISTRY: dict[str, type[DocumentLoader]] = {
    ".pdf": PDFLoader,
    ".md": MarkdownLoader,
    ".markdown": MarkdownLoader,
    ".mdx": MarkdownLoader,
}


def get_loader(file_path: Path) -> DocumentLoader:
    """Get the appropriate loader for a file based on its extension.

    Args:
        file_path: Path to the file.

    Returns:
        An instantiated DocumentLoader.

    Raises:
        ValueError: If the file extension is not supported.
    """
    suffix = file_path.suffix.lower()
    loader_cls = _LOADER_REGISTRY.get(suffix)
    if loader_cls is None:
        supported = ", ".join(sorted(_LOADER_REGISTRY.keys()))
        raise ValueError(
            f"Unsupported file extension '{suffix}'. "
            f"Supported extensions: {supported}"
        )
    logger.debug("Selected loader %s for extension '%s'", loader_cls.__name__, suffix)
    return loader_cls()


def get_supported_extensions() -> list[str]:
    """Return a sorted list of supported file extensions."""
    return sorted(_LOADER_REGISTRY.keys())


def register_loader(extension: str, loader_cls: type[DocumentLoader]) -> None:
    """Register a custom loader for a file extension.

    This allows extending the pipeline with new document types
    (e.g., .docx, .html, .txt) without modifying existing code.

    Args:
        extension: File extension (e.g., '.html'). Must start with a dot.
        loader_cls: DocumentLoader subclass to handle this extension.
    """
    if not extension.startswith("."):
        extension = f".{extension}"
    _LOADER_REGISTRY[extension.lower()] = loader_cls
    logger.info("Registered loader %s for extension '%s'", loader_cls.__name__, extension)
