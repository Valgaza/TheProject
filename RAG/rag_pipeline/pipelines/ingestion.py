"""Ingestion pipeline — end-to-end document loading, chunking, embedding, and storage.

This orchestrator wires together the loader registry, chunking engine,
embedding service, and vector store into a single cohesive pipeline.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from rag_pipeline.chunking.markdown_chunker import MarkdownChunker
from rag_pipeline.chunking.recursive_chunker import RecursiveChunker
from rag_pipeline.config import settings
from rag_pipeline.embeddings.fastembed_service import FastEmbedService
from rag_pipeline.loaders.registry import get_loader, get_supported_extensions
from rag_pipeline.models import DocumentType, IngestionStats
from rag_pipeline.vectorstore.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """End-to-end pipeline: files → chunks → embeddings → Qdrant.

    Usage:
        pipeline = IngestionPipeline()
        stats = pipeline.ingest("/path/to/document.pdf")
        stats = pipeline.ingest_directory("/path/to/docs/")
    """

    def __init__(
        self,
        embedding_service: FastEmbedService | None = None,
        vector_store: QdrantStore | None = None,
    ):
        """Initialize with optional dependency injection.

        If not provided, creates default instances from settings.
        """
        self._embedding_service = embedding_service or FastEmbedService()
        self._vector_store = vector_store or QdrantStore()
        self._collection_ready = False

        # Chunkers (selected per document type)
        self._recursive_chunker = RecursiveChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        self._markdown_chunker = MarkdownChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

    def _ensure_collection(self) -> None:
        """Ensure the Qdrant collection exists (lazy, runs once)."""
        if not self._collection_ready:
            dim = self._embedding_service.get_dense_dimension()
            self._vector_store.ensure_collection(dense_dim=dim)
            self._collection_ready = True

    def ingest(self, file_path: str | Path) -> IngestionStats:
        """Ingest a single file through the full pipeline.

        Args:
            file_path: Path to the file to ingest.

        Returns:
            IngestionStats with processing metrics.
        """
        start_time = time.time()
        path = Path(file_path)
        stats = IngestionStats()

        try:
            # Step 1: Load
            loader = get_loader(path)
            documents = loader.load(path)
            stats.documents_processed = len(documents)
            logger.info("Loaded %d documents from %s", len(documents), path.name)

            if not documents:
                stats.elapsed_seconds = time.time() - start_time
                return stats

            # Step 2: Chunk (auto-select strategy based on document type)
            doc_type = documents[0].metadata.document_type
            if doc_type == DocumentType.MARKDOWN:
                chunker = self._markdown_chunker
            else:
                chunker = self._recursive_chunker

            chunks = chunker.chunk_batch(documents)
            stats.chunks_created = len(chunks)
            logger.info("Created %d chunks", len(chunks))

            if not chunks:
                stats.elapsed_seconds = time.time() - start_time
                return stats

            # Step 3: Embed
            texts = [chunk.content for chunk in chunks]
            embeddings = self._embedding_service.embed_texts(texts)

            # Link embeddings to chunks
            for chunk, emb in zip(chunks, embeddings):
                emb.chunk_id = chunk.id

            stats.embeddings_generated = len(embeddings)
            logger.info("Generated %d embeddings", len(embeddings))

            # Step 4: Store
            self._ensure_collection()
            upserted = self._vector_store.upsert(chunks, embeddings)
            stats.points_upserted = upserted
            logger.info("Upserted %d points", upserted)

        except Exception as e:
            logger.error("Ingestion error for %s: %s", path, e)
            stats.errors.append(str(e))

        stats.elapsed_seconds = time.time() - start_time
        return stats

    def ingest_directory(self, dir_path: str | Path) -> IngestionStats:
        """Ingest all supported files in a directory.

        Args:
            dir_path: Path to the directory.

        Returns:
            Aggregated IngestionStats across all files.
        """
        start_time = time.time()
        directory = Path(dir_path)

        if not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")

        total_stats = IngestionStats()
        supported = set(get_supported_extensions())

        # Collect all supported files
        files = [
            f for f in sorted(directory.rglob("*"))
            if f.is_file() and f.suffix.lower() in supported
        ]

        logger.info(
            "Found %d supported files in %s", len(files), directory,
        )

        for file_path in files:
            logger.info("Ingesting: %s", file_path.name)
            file_stats = self.ingest(file_path)

            # Aggregate stats
            total_stats.documents_processed += file_stats.documents_processed
            total_stats.chunks_created += file_stats.chunks_created
            total_stats.embeddings_generated += file_stats.embeddings_generated
            total_stats.points_upserted += file_stats.points_upserted
            total_stats.errors.extend(file_stats.errors)

        total_stats.elapsed_seconds = time.time() - start_time
        return total_stats
