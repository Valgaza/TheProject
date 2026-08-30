"""Reading memory back out.

Three things in graphiti 0.29 shape this, all verified against the installed
source and against what actually landed in the graph.

**Never call ``Graphiti.search()``.** It does this (graphiti.py:1516):

    search_config = EDGE_HYBRID_SEARCH_RRF if center_node_uuid is None else ...
    search_config.limit = num_results

``EDGE_HYBRID_SEARCH_RRF`` is a module-level singleton, so that assignment
mutates shared state. Two concurrent searches with different limits overwrite
each other. Use ``search_()`` and hand it a deep copy.

**Do not start on the cross-encoder recipe**, which is ``search_()``'s own
default. ``COMBINED_HYBRID_SEARCH_CROSS_ENCODER`` reranks with an LLM
classification call *per candidate passage*, so latency and cost scale with how
much the graph knows. RRF is arithmetic over ranks bm25 and cosine already
produced, and costs nothing.

**Search nodes as well as edges.** This one came from the graph rather than the
source. Ingesting "Aarya is a final year B.Tech student who prefers terse
answers" produced *no edges at all* -- graphiti stored it as the summary of a
single ``Aarya`` node, because it is a set of attributes about one entity rather
than a relationship between two. An edge-only recipe cannot see it. Since
standing facts about a person are precisely what the user tier exists to hold,
edge-only retrieval would have missed the half of memory that matters most,
while looking like it worked on project facts.

**Search episodes too, despite the duplication.** Entity summaries do not
accumulate. Four facts about Aarya were ingested and confirmed written, but the
``Aarya`` node's summary held only the first of them -- a later fact about the
same entity resolves onto the existing node and replaces its summary rather than
extending it. The facts were not lost, only unreachable: every one was still
sitting in its ``Episodic`` node verbatim. Episodes are the durable record and
the distilled node summary is a lossy view of it, which is the opposite of the
assumption this module started with. The duplication that argued against
including them is real, and is what the dedupe pass exists to absorb.

Episodes are BM25-only -- graphiti offers no vector search over episode content --
so they answer keyword-shaped questions and miss paraphrases. That is still
strictly better than the facts being unreachable.
"""

from __future__ import annotations

from graphiti_core.search.search_config import (
    EdgeReranker,
    EdgeSearchConfig,
    EdgeSearchMethod,
    EpisodeReranker,
    EpisodeSearchConfig,
    EpisodeSearchMethod,
    NodeReranker,
    NodeSearchConfig,
    NodeSearchMethod,
    SearchConfig,
)

DEFAULT_LIMIT = 8

# graphiti's own default. Left alone deliberately.
#
# A sweep at 0.6 / 0.45 / 0.3 against the graph as it stands was inconclusive by
# construction: with a single fact stored, 0.45 and below matched *every* query
# including "how do I bake sourdough bread", and even 0.6 returned the fact for
# "what is the capital of France". There is nothing for the ranking to
# discriminate against yet, so any number picked now would be fitted to noise.
# Exposed as a setting so phase 7 can sweep it against a realistic corpus, which
# is the first point at which the answer means anything.
DEFAULT_MIN_SCORE = 0.6


def _recipe(min_score: float) -> SearchConfig:
    """Edges, nodes and episodes, with RRF reranking.

    Communities stay excluded: nothing calls ``build_communities``, so there are
    none to find.
    """
    return SearchConfig(
        edge_config=EdgeSearchConfig(
            search_methods=[EdgeSearchMethod.bm25, EdgeSearchMethod.cosine_similarity],
            reranker=EdgeReranker.rrf,
            sim_min_score=min_score,
        ),
        node_config=NodeSearchConfig(
            search_methods=[NodeSearchMethod.bm25, NodeSearchMethod.cosine_similarity],
            reranker=NodeReranker.rrf,
            sim_min_score=min_score,
        ),
        episode_config=EpisodeSearchConfig(
            # bm25 only; graphiti offers no vector search over episode content.
            search_methods=[EpisodeSearchMethod.bm25],
            reranker=EpisodeReranker.rrf,
        ),
    )


def build_search_config(
    limit: int = DEFAULT_LIMIT, min_score: float = DEFAULT_MIN_SCORE
) -> SearchConfig:
    """A fresh config per search, never a shared recipe. See the module docstring."""
    config = _recipe(min_score)
    config.limit = limit
    return config
