# RAG Pipeline

Production-grade Retrieval-Augmented Generation pipeline with Qdrant hybrid search, cross-encoder reranking, and LLM-agnostic context assembly.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Ingest documents
rag ingest sample_docs/sample.md

# Query
rag query "What is hybrid search?"

# Check status
rag status
```

## Architecture

```
Documents → Loaders → Chunking → Embeddings → Qdrant (dense + sparse)
                                                    ↓
User Query → Embed → Hybrid Search → Rerank → Context Assembly → LLM
```
