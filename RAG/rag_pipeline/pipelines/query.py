"""Query pipeline — end-to-end query processing, retrieval, reranking, and context assembly.

Takes a user question and returns assembled context ready for any LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rag_pipeline.config import settings
from rag_pipeline.context.assembler import ContextAssembler
from rag_pipeline.embeddings.fastembed_service import FastEmbedService
from rag_pipeline.models import QueryResult
from rag_pipeline.retrieval.reranker import CrossEncoderReranker
from rag_pipeline.retrieval.retriever import HybridRetriever
from rag_pipeline.vectorstore.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


class QueryPipeline:
    """End-to-end pipeline: query → embed → search → rerank → assemble.

    Usage:
        pipeline = QueryPipeline()
        result = pipeline.query("What are the key findings?")
        print(result.assembled_context.prompt)  # ready for any LLM
    """

    def __init__(
        self,
        embedding_service: FastEmbedService | None = None,
        vector_store: QdrantStore | None = None,
        reranker: CrossEncoderReranker | None = None,
        context_assembler: ContextAssembler | None = None,
    ):
        """Initialize with optional dependency injection."""
        _embeddings = embedding_service or FastEmbedService()
        _store = vector_store or QdrantStore()

        self._retriever = HybridRetriever(
            embedding_service=_embeddings,
            vector_store=_store,
        )
        self._reranker = reranker or CrossEncoderReranker()
        self._assembler = context_assembler or ContextAssembler()

    def query(
        self,
        question: str,
        top_k: int | None = None,
        top_n: int | None = None,
        filters: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Process a query through the full RAG pipeline.

        Args:
            question: The user's natural language question.
            top_k: Number of candidates to retrieve (default from settings).
            top_n: Number of final results after reranking (default from settings).
            filters: Optional metadata filters for retrieval.

        Returns:
            QueryResult with assembled context, sources, and timing info.
        """
        start_time = time.time()
        k = top_k or settings.retrieval_top_k
        n = top_n or settings.rerank_top_n

        logger.info("Query pipeline: '%s'", question[:100])

        # Step 1: Retrieve candidates via hybrid search
        retrieval_results = self._retriever.retrieve(
            query=question,
            top_k=k,
            filters=filters,
        )

        if not retrieval_results:
            logger.warning("No retrieval results for query")
            return QueryResult(
                query=question,
                assembled_context=self._assembler.assemble(question, []),
                elapsed_seconds=time.time() - start_time,
            )

        # Step 2: Rerank with cross-encoder
        reranked_results = self._reranker.rerank(
            query=question,
            results=retrieval_results,
            top_n=n,
        )

        # Step 3: Assemble context for LLM
        assembled = self._assembler.assemble(question, reranked_results)

        elapsed = time.time() - start_time
        logger.info(
            "Query pipeline complete in %.2fs (%d retrieved → %d reranked → %d in context)",
            elapsed,
            len(retrieval_results),
            len(reranked_results),
            assembled.total_chunks_used,
        )

        return QueryResult(
            query=question,
            assembled_context=assembled,
            retrieval_results=retrieval_results,
            reranked_results=reranked_results,
            elapsed_seconds=elapsed,
        )
