---
title: RAG Pipeline Sample Document
author: RAG Pipeline Test Suite
date: 2026-03-25
---

# Introduction to Retrieval-Augmented Generation

Retrieval-Augmented Generation (RAG) is a technique that enhances Large Language Models by providing them with relevant context retrieved from external knowledge sources. This approach addresses the fundamental limitation of LLMs: their knowledge is frozen at training time.

## How RAG Works

RAG operates in two main phases: indexing and retrieval.

### Indexing Phase

During indexing, documents are processed through several steps:

1. **Document Loading**: Raw files (PDFs, Markdown, HTML) are parsed into structured text
2. **Chunking**: Documents are split into smaller, semantically coherent pieces
3. **Embedding**: Each chunk is converted into a dense vector representation
4. **Storage**: Vectors are stored in a vector database for fast similarity search

### Retrieval Phase

When a user asks a question:

1. The query is embedded using the same model
2. Similar chunks are retrieved via vector similarity search
3. Retrieved chunks are optionally reranked for precision
4. The most relevant chunks are assembled into context for the LLM

## Key Components

### Vector Databases

Vector databases like Qdrant, Pinecone, and ChromaDB enable fast approximate nearest neighbor search. Qdrant specifically supports hybrid search combining dense and sparse vectors.

### Embedding Models

Modern embedding models convert text into high-dimensional vectors where semantic similarity is preserved as geometric proximity. Models like BGE, E5, and GTE are popular choices.

### Reranking

Cross-encoder rerankers process (query, passage) pairs jointly for much more accurate relevance scoring than bi-encoder embeddings alone. This dramatically improves precision at the cost of some latency.

## Advanced Techniques

### Hybrid Search

Combining dense (semantic) and sparse (keyword) retrieval yields better results than either approach alone. Reciprocal Rank Fusion (RRF) is a common method for merging results from different retrieval methods.

### Chunking Strategies

The choice of chunking strategy significantly impacts retrieval quality:

- **Recursive splitting**: Respects natural text boundaries
- **Semantic chunking**: Splits at meaning shifts
- **Document-aware chunking**: Uses document structure (headings, sections)

Overlap between chunks ensures that information at boundaries is preserved during retrieval.
