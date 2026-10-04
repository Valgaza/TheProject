"""Shared test fixtures and configuration."""

from __future__ import annotations

import pytest
from pathlib import Path

SAMPLE_DOCS_DIR = Path(__file__).parent.parent / "sample_docs"
SAMPLE_MD = SAMPLE_DOCS_DIR / "sample.md"


@pytest.fixture
def sample_md_path() -> Path:
    """Path to the sample markdown file."""
    return SAMPLE_MD


@pytest.fixture
def sample_text() -> str:
    """Short sample text for unit tests."""
    return (
        "Retrieval-Augmented Generation (RAG) is a technique that enhances "
        "Large Language Models by providing relevant context from external sources. "
        "Vector databases enable fast approximate nearest neighbor search. "
        "Qdrant supports hybrid search combining dense and sparse vectors."
    )


@pytest.fixture
def long_sample_text() -> str:
    """Longer text that will definitely be split into multiple chunks."""
    paragraphs = [
        f"This is paragraph {i}. It contains important information about topic {i}. "
        f"The details of topic {i} are essential for understanding the broader context. "
        f"Multiple sentences ensure this paragraph has sufficient length for testing."
        for i in range(20)
    ]
    return "\n\n".join(paragraphs)
