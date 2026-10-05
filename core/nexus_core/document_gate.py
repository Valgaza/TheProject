"""Telling the graph what a document is about.

The vector store already holds what a document *says*. This writes what the
person now *knows about* -- one or two durable facts naming the subject, at
the document's own tier.

The distinction is the whole design. Handing a PDF to graphiti would extract
hundreds of entities and produce a worse, lossier copy of the corpus in a
store built for a different job. The graph's job here is to answer "does this
person have anything on consensus protocols?", not to answer questions from
the document -- retrieval already does that, better.

So the gate is deliberately stingy: at most three facts, each naming the
document, none of them summarising its argument. If you find yourself wanting
the graph to hold the content, the thing you want is retrieval.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from .memory_client import MemoryClient
from .models import Document, Profile
from .rag_client import RagClient
from .providers.base import Provider

logger = logging.getLogger(__name__)

# Enough of a document to know what it is about. A whole book would blow the
# context and buy nothing -- the subject of a document is almost always
# settled by its opening.
EXCERPT_CHARS = 4000

MAX_FACTS = 3

# Enforced by the MLX runtime's constrained decoding; _parse still validates.
DOCUMENT_GATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "maxItems": MAX_FACTS, "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["facts", "topics", "reason"],
}

DOCUMENT_GATE_PROMPT = """\
You are cataloguing a document someone added to their personal assistant.

Return ONLY a JSON object, no prose and no code fence:
{"facts": [string], "topics": [string], "reason": string}

facts: at most three sentences recording what this person now has reference
material on. Each must be one self-contained sentence in the third person,
naming the person by the name given below and naming the document by its
filename. Write about the person's holdings, never about the document's
argument.

  Good: "Aarya has a reference document, raft.pdf, on the Raft consensus
         algorithm and leader election."
  Bad:  "Raft elects a leader using randomised timeouts."

The second is a fact from the document. Those belong in the retrieval index,
not here -- this graph records what someone knows about, not what is true
inside their files. A fact that would still make sense with the document
deleted is the wrong kind of fact.

If the document is too short, too generic, or too corrupted to name a
subject, return an empty facts list and say why in reason.

topics: two to six short noun phrases naming the subject areas, lowercase.
These are for the reader, not the graph.

reason: a short phrase explaining the decision, for a human reading logs.
"""


def _parse(raw: str) -> tuple[list[str], list[str], str]:
    """Read the gate's JSON, tolerating a code fence.

    Fails soft, exactly like the write gate's parser. A model that returns
    prose means this document contributes nothing to the graph, which is a
    smaller problem than a background task raising into the void.
    """
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Document gate returned unparseable output: %r", raw[:160])
        return [], [], "gate returned unparseable output"

    facts = [
        str(f).strip()
        for f in (data.get("facts") or [])
        if isinstance(f, (str, int, float)) and str(f).strip()
    ][:MAX_FACTS]
    topics = [str(t).strip() for t in (data.get("topics") or []) if str(t).strip()]
    return facts, topics, str(data.get("reason", ""))


async def sync_document_to_graph(
    *,
    session_factory: async_sessionmaker,
    memory: MemoryClient,
    rag: RagClient,
    provider: Provider,
    document_id: uuid.UUID,
    gate_model: str | None,
) -> None:
    """Catalogue one ingested document into the graph.

    Runs after the upload response, like the write gate runs after a reply.
    Swallows everything: this is a background task, and an exception here
    would be logged by the framework and otherwise vanish, so it is caught
    where there is enough context to say something useful.

    The document's own tier decides the scope. A file uploaded into a project
    writes project-scoped facts; a loose one writes user-scoped facts visible
    everywhere -- the same asymmetry the write gate applies to conversation,
    for the same reason.
    """
    try:
        async with session_factory() as session:
            document = await session.get(Document, document_id)
            if document is None:
                # Loud, because this used to be a bare return and it hid a
                # real bug: a task scheduled before its row was committed
                # found nothing and did nothing, with no trace anywhere.
                logger.warning(
                    "Document %s not found; nothing catalogued. If this fired "
                    "right after an upload, the row was not committed before "
                    "the task was scheduled.",
                    document_id,
                )
                return
            profile = await session.get(Profile, document.user_id)
            subject = (profile.display_name if profile else None) or "The user"
            file_name = document.file_name
            user_id = document.user_id
            project_id = document.project_id

        # Read back out of the chunks rather than kept from the upload: the
        # bytes were a PDF, and they have already been parsed, chunked and
        # cleaned on the other side of the ingest. Re-parsing here would mean
        # core growing a PDF dependency to redo work RAG just did.
        excerpt = await rag.excerpt(document_id, chars=EXCERPT_CHARS)
        if not excerpt:
            logger.info("No excerpt for %s; nothing to catalogue", document_id)
            return

        scope = "project" if project_id is not None else "user"
        try:
            raw = await provider.chat(
                [
                    {"role": "system", "content": DOCUMENT_GATE_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"The person is named: {subject}\n"
                            f"Filename: {file_name}\n\n"
                            f"Opening of the document:\n{excerpt}"
                        ),
                    },
                ],
                model=gate_model,
                # Background cataloguing: nobody is waiting on it, and it
                # must never slow the next answer (or the first thing said
                # in a voice session right after an upload).
                priority="bulk",
                temperature=0.0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "document_gate", "schema": DOCUMENT_GATE_SCHEMA},
                },
            )
        except Exception as exc:
            logger.warning("Document gate call failed for %s: %s", document_id, exc)
            return

        facts, topics, reason = _parse(raw)
        if not facts:
            logger.info("Document gate kept nothing for %s: %s", document_id, reason)
            return

        written = 0
        for statement in facts:
            result = await memory.ingest(
                statement, scope=scope, user_id=user_id, project_id=project_id
            )
            if getattr(result, "error", None):
                logger.warning("Graph write failed for %s: %s", document_id, result.error)
            else:
                written += 1

        logger.info(
            "document catalogued  file=%s scope=%s facts=%d/%d topics=%s",
            file_name, scope, written, len(facts), ", ".join(topics[:4]),
        )

        if written:
            # Stamped only on success, so a null here always means "the graph
            # does not know about this file" rather than "we are not sure".
            async with session_factory() as session:
                document = await session.get(Document, document_id)
                if document is not None:
                    document.graph_synced_at = datetime.now(timezone.utc)
                    await session.commit()

    except Exception:
        logger.exception("Cataloguing document %s failed", document_id)


