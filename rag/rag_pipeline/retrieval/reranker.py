"""Cross-encoder reranker for precision refinement.

After initial retrieval returns candidates, the reranker scores each
(query, passage) pair with a cross-encoder model for much more accurate
relevance scoring than bi-encoder similarity alone.

This is the single biggest quality improvement in a RAG pipeline.

Runs on ONNX Runtime through fastembed, not on PyTorch through
sentence-transformers. Same model weights (Xenova's ONNX export of
ms-marco-MiniLM-L-12-v2), same scores to four decimal places -- checked
2026-10-04 -- without loading torch into the service, which cost memory the
language model needs and about 14 s on every cold start.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from rag_pipeline.config import settings
from rag_pipeline.models import RerankedResult, SearchResult

if TYPE_CHECKING:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

logger = logging.getLogger(__name__)


def _onnx_name(model_name: str) -> str:
    """The ONNX export of a sentence-transformers cross-encoder name, so an
    older RERANKER_MODEL setting keeps meaning the same model."""
    if model_name.startswith("cross-encoder/"):
        return "Xenova/" + model_name.split("/", 1)[1]
    return model_name


class CrossEncoderReranker:
    """Reranker using a cross-encoder on ONNX Runtime (fastembed).

    Model: ms-marco-MiniLM-L-12-v2 (ONNX export: Xenova/ms-marco-MiniLM-L-12-v2)
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
        self._model_name = _onnx_name(model_name or settings.reranker_model)
        self._top_n = top_n or settings.rerank_top_n
        self._score_threshold = (
            score_threshold if score_threshold is not None
            else settings.rerank_score_threshold
        )
        self._model: TextCrossEncoder | None = None

    @property
    def model(self) -> TextCrossEncoder:
        """Lazy-load the cross-encoder model."""
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            logger.info("Loading cross-encoder model: %s", self._model_name)
            self._model = TextCrossEncoder(self._model_name)
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

        # Score every (query, passage) pair. The model returns raw logits
        # (can be negative); a sigmoid turns them into 0-1 probabilities,
        # which is the scale core's CORE_RAG_MIN_SCORE floor is set on.
        raw_scores = list(self.model.rerank(query, [result.content for result in results]))

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
