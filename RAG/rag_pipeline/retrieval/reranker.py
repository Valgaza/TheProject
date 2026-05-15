"""Cross-encoder reranker for precision refinement.

After initial retrieval returns candidates, the reranker scores each
(query, passage) pair with a cross-encoder model for much more accurate
relevance scoring than bi-encoder similarity alone.

This is the single biggest quality improvement in a RAG pipeline.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from rag_pipeline.config import settings
from rag_pipeline.models import RerankedResult, SearchResult

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """Reranker using sentence-transformers CrossEncoder.

    Model: cross-encoder/ms-marco-MiniLM-L-12-v2
        - Trained on MS MARCO passage ranking
        - NDCG@10 of 74.31 on TREC DL 2019
        - Fast enough for real-time reranking of 20-50 candidates

    The cross-encoder sees the full (query, passage) pair together,
    enabling much deeper understanding of relevance than separate
    embeddings can provide.
    """

    def __init__(
        self,
        model_name: str | None = None,
        top_n: int | None = None,
        score_threshold: float | None = None,
    ):
        """Initialize the reranker.

        Args:
            model_name: Cross-encoder model name (default from settings).
            top_n: Number of top results to return after reranking.
            score_threshold: Minimum score to include a result.
        """
        self._model_name = model_name or settings.reranker_model
        self._top_n = top_n or settings.rerank_top_n
        self._score_threshold = (
            score_threshold if score_threshold is not None
            else settings.rerank_score_threshold
        )
        self._model: CrossEncoder | None = None

    @property
    def model(self) -> CrossEncoder:
        """Lazy-load the cross-encoder model."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info("Loading cross-encoder model: %s", self._model_name)
            self._model = CrossEncoder(self._model_name)
        return self._model

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_n: int | None = None,
    ) -> list[RerankedResult]:
        """Rerank search results using the cross-encoder.

        Args:
            query: The original user query.
            results: Initial retrieval results to rerank.
            top_n: Number of results to return (overrides default).

        Returns:
            Reranked results sorted by cross-encoder score, filtered by threshold.
        """
        if not results:
            return []

        n = top_n or self._top_n
        logger.info("Reranking %d candidates → top %d", len(results), n)

        # Build (query, passage) pairs for the cross-encoder
        pairs = [(query, result.content) for result in results]

        # Score all pairs.
        # sentence-transformers v5 returns raw logits (can be negative).
        # Apply sigmoid to convert to a 0-1 probability score.
        raw_scores = self.model.predict(pairs)

        def _sigmoid(x: float) -> float:
            return 1.0 / (1.0 + math.exp(-x))

        # Build reranked results — no threshold, always return top-n
        reranked: list[RerankedResult] = []
        for result, raw in zip(results, raw_scores):
            reranked.append(RerankedResult(
                chunk_id=result.chunk_id,
                content=result.content,
                original_score=result.score,
                rerank_score=_sigmoid(float(raw)),
                metadata=result.metadata,
            ))

        # Sort by rerank score (descending) and take top-n
        reranked.sort(key=lambda r: r.rerank_score, reverse=True)
        reranked = reranked[:n]

        logger.info(
            "Reranking complete: %d results (top score: %.4f)",
            len(reranked),
            reranked[0].rerank_score if reranked else 0.0,
        )
        return reranked
