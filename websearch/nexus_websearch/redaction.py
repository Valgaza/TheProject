"""Making a message safe to send to a public search engine.

Moved here from core (it was core/nexus_core/web_client.py): the code that
redacts what leaves the machine now lives in the only process allowed to send
it. Flag 1 in docs/IMPORTANT_BUSINESS_LOGIC_FLAGS.md settled that redaction
belongs inside the one client that talks to the internet, so no caller can
forget it. Two layers, in order:

1. A model call rewrites the message as a generic topic query. This is what
   catches a third party's name ("[patient]'s stage 3 lymphoma" becomes
   "stage 3 lymphoma treatment options"): nothing rule-based can know that an
   arbitrary word is someone's name.
2. Whatever the model returned, a deterministic pass strips what the system
   can vouch for: the signed-in person's own names and email, and anything
   carrying five or more digits (case numbers, MRNs, phone numbers). It runs
   on the model's output, so a rewrite that leaks is still caught.

If the rewrite fails or comes back empty, nothing is sent. Searching with the
raw message is never the fallback.
"""

from __future__ import annotations

import re

MAX_QUERY_CHARS = 200

REWRITE_PROMPT = """\
Turn the user's message into a web search query about its general topic.

Return ONLY the query text: no quotes, no explanation.

Never include any person's name, initials, email, phone number, address,
employer, case number, record number, or any other detail that identifies a
specific person, client or matter. Keep only the subject a public search
engine needs: the condition, the legal concept, the technology, the event.

Example
  Message: search the internet for the latest treatment options for Priya
           Nair's stage 3 lymphoma, MRN 00482913
  Query:   latest treatment options stage 3 lymphoma
"""

_EMAIL = re.compile(r"\S+@\S+")
_TOKEN = re.compile(r"\S+")


def strip_identifiers(query: str, known_names: list[str]) -> tuple[str, list[str]]:
    """The deterministic layer. Returns the cleaned query and what it removed."""
    removed: list[str] = []

    def drop(match: re.Match) -> str:
        removed.append(match.group(0))
        return " "

    query = _EMAIL.sub(drop, query)

    # Each part of a name on its own, so "Aarya" goes even when the model kept
    # only the first name. Parts under three letters are skipped: stripping
    # every "Al" or "Li" would gut ordinary queries.
    parts = {p for name in known_names for p in re.split(r"[\s@._-]+", name) if len(p) >= 3}
    for part in sorted(parts, key=len, reverse=True):
        query = re.sub(rf"\b{re.escape(part)}(?:'s)?\b", drop, query, flags=re.IGNORECASE)

    def drop_numbered(match: re.Match) -> str:
        token = match.group(0)
        if sum(ch.isdigit() for ch in token) >= 5:
            removed.append(token)
            return " "
        return token

    query = _TOKEN.sub(drop_numbered, query)
    return " ".join(query.split())[:MAX_QUERY_CHARS], removed
