"""Context assembly and prompt building — LLM-agnostic output layer.

This is the bridge between retrieval and any LLM. It takes reranked
chunks and assembles them into a structured context block with source
attribution, ready for any language model to consume.
"""

from __future__ import annotations

import logging

from rag_pipeline.config import settings
from rag_pipeline.models import AssembledContext, RerankedResult

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Default prompt template — designed for instruction-following LLMs
# -------------------------------------------------------------------------
DEFAULT_SYSTEM_TEMPLATE = """You are a knowledgeable assistant. Answer the user's question accurately using ONLY the provided context. If the context doesn't contain enough information to answer fully, say so clearly.

Rules:
- Base your answer strictly on the provided context
- Cite the source when referencing specific information
- If multiple sources provide relevant information, synthesize them
- Be concise but thorough"""

DEFAULT_CONTEXT_HEADER = "--- Retrieved Context ---"
DEFAULT_CONTEXT_FOOTER = "--- End of Context ---"
DEFAULT_CHUNK_TEMPLATE = "[Source: {source} | Chunk {chunk_index}]\n{content}"


class ContextAssembler:
    """Assembles retrieved chunks into LLM-ready context and prompts.

    Features:
        - Source attribution per chunk (filename, page, chunk index)
        - Character-aware truncation (respects max context length)
        - Configurable prompt templates
        - Clean output: no formatting dependencies on specific LLMs
    """

    def __init__(
        self,
        system_template: str | None = None,
        context_header: str | None = None,
        context_footer: str | None = None,
        chunk_template: str | None = None,
        max_context_chars: int | None = None,
    ):
        self._system_template = system_template or DEFAULT_SYSTEM_TEMPLATE
        self._context_header = context_header or DEFAULT_CONTEXT_HEADER
        self._context_footer = context_footer or DEFAULT_CONTEXT_FOOTER
        self._chunk_template = chunk_template or DEFAULT_CHUNK_TEMPLATE
        self._max_context_chars = max_context_chars or settings.max_context_chars

    def assemble(
        self,
        query: str,
        results: list[RerankedResult],
    ) -> AssembledContext:
        """Assemble reranked results into a structured context and prompt.

        Args:
            query: The original user query.
            results: Reranked results to include as context.

        Returns:
            AssembledContext with context text, full prompt, and source list.
        """
        if not results:
            return AssembledContext(
                context_text="",
                prompt=self._build_prompt(query, "No relevant context found."),
                sources=[],
                total_chunks_used=0,
                total_chars=0,
            )

        # Format each chunk with source attribution
        formatted_chunks: list[str] = []
        sources: list[dict] = []
        total_chars = 0

        for result in results:
            # Build source label
            source_parts = [result.metadata.get("file_name", "unknown")]
            page = result.metadata.get("page_number")
            if page is not None:
                source_parts.append(f"p.{page}")
            source_label = " | ".join(source_parts)

            # Format the chunk
            formatted = self._chunk_template.format(
                source=source_label,
                chunk_index=result.metadata.get("chunk_index", 0),
                content=result.content,
            )

            # Check if adding this chunk exceeds the limit
            new_total = total_chars + len(formatted)
            if new_total > self._max_context_chars and formatted_chunks:
                logger.info(
                    "Context truncated at %d chars (limit: %d)",
                    total_chars, self._max_context_chars,
                )
                break

            formatted_chunks.append(formatted)
            total_chars = new_total
            sources.append({
                "file_name": result.metadata.get("file_name", "unknown"),
                "page_number": page,
                "chunk_index": result.metadata.get("chunk_index", 0),
                "heading_path": result.metadata.get("heading_path", ""),
                "rerank_score": result.rerank_score,
            })

        # Build context block
        context_text = "\n\n".join([
            self._context_header,
            "\n\n".join(formatted_chunks),
            self._context_footer,
        ])

        # Build full prompt
        prompt = self._build_prompt(query, context_text)

        return AssembledContext(
            context_text=context_text,
            prompt=prompt,
            sources=sources,
            total_chunks_used=len(formatted_chunks),
            total_chars=total_chars,
        )

    def _build_prompt(self, query: str, context: str) -> str:
        """Build the full prompt string.

        Format is designed to work with any instruction-following LLM:
            System message → Context → User query
        """
        return f"""{self._system_template}

{context}

User question: {query}"""
