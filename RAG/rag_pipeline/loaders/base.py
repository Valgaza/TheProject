"""Abstract base class for document loaders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from rag_pipeline.models import Document


class DocumentLoader(ABC):
    """Base interface for all document loaders.

    Every loader must implement `load()`, which reads a file and returns
    one or more Document objects. A PDF loader may return one Document per page;
    a Markdown loader typically returns a single Document.
    """

    @abstractmethod
    def load(self, file_path: Path) -> list[Document]:
        """Load a file and return a list of documents.

        Args:
            file_path: Absolute path to the file to load.

        Returns:
            A list of Document objects with content and metadata populated.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file format is unsupported by this loader.
        """

    def _validate_path(self, file_path: Path) -> Path:
        """Validate that the file exists and return a resolved path."""
        resolved = file_path.resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"Not a file: {resolved}")
        return resolved
