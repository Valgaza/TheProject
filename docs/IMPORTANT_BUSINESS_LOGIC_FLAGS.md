# Important Business Logic Flags

Decisions that affect real product behavior or safety — not just code style — get tracked here instead of only living in a code comment, so they don't get lost. Each entry gets a priority once we've discussed it.

## Priority scale (proposed, open to changing this)

- **P0** — Could leak client data or produce a clearly wrong/unsafe answer. Must resolve before any real user touches this.
- **P1** — Important, should resolve before this feature ships, but doesn't block ongoing dev/testing.
- **P2** — Worth doing, but a defensible v1 exists without it.
- **P3** — Nice to have / polish.

---

## Open flags

### 1. Web search queries can leak client-identifying details

**Status:** mechanism implemented in `core` (2026-09-26), priority still to assign. The standalone `orchestrator/` service that sent raw queries was deleted on 2026-10-04.
**Raised:** 2026-08-10
**Where:** `core/nexus_core/web_client.py` (the only code that talks to SearXNG), called from `core/nexus_core/chat_service.py` on a `web` route. Originally raised against the since-deleted `orchestrator/app/main.py`, which passed the raw message straight to its web client.

**Problem:**

The internet pipeline is user-triggered (a button, or phrases like "search the internet") — that's a good first privacy boundary, since the user consciously opts into a web request. But "the user chose to search" doesn't mean "the user chose what text goes out." When this was raised, `orchestrator/app/main.py` passed the user's raw message straight to `web_client.search()`, which sends it directly to SearXNG, which forwards it to real external search engines. There was zero filtering of that text.

Concretely: a doctor isn't being careless by typing *"search the internet for the latest treatment options for [patient name]'s stage 3 lymphoma"* — that's just how people naturally phrase a question to a chatbot. The moment that string leaves the machine, the patient's name is now sitting inside an outbound request to a third-party search engine, subject to that search engine's own logging policies, completely outside our control. That breaks the core "client data never leaves this machine" premise of the whole product — for this one pipeline specifically.

It's an easy thing to miss in testing: an artificial test query like "search the internet for AI news" works fine and never reveals the problem, because it never contains anything sensitive to begin with. The bug only shows up under realistic use.

**Proposed direction (from user, 2026-08-10):** either the orchestrator or the LLM should strip identifying details (patient/client name) before the query goes out, and search only for the general topic (e.g. the disease/condition/legal concept), not the identifying context around it.

**Decided direction (user, 2026-08-10):** redaction logic belongs specifically inside the web client (then `orchestrator/app/clients/web_client.py`, now `core/nexus_core/web_client.py`), not a vaguer "somewhere in the orchestrator", so it's enforced right at the one place that actually talks to the open internet — every caller of `web_client.search()` gets it for free, with no way to accidentally bypass it. There should also be a corresponding piece on the LLM's side (an explicit instruction never to include names/IDs/case numbers in what it asks the web tool to search for), as defense in depth on top of the enforced `web_client.py` layer, not instead of it.

**Settled 2026-09-26 (see below):** the actual redaction mechanism — options discussed were rule-based stripping of known names (e.g. pulled from the graph) vs. an LLM call whose only job is to rewrite the query generically. Both were used.

**Decision:** _priority not yet made._

**Implemented (2026-09-26), in `core/nexus_core/web_client.py`:** web search moved into `core` as a handler, with redaction enforced inside the client as decided above. It uses both mechanisms, in order:

1. The LLM rewrite. A small model call turns the message into a generic topic query, with an explicit instruction never to include names, IDs or case numbers. This is the only layer that can catch a third party's name.
2. Rule-based stripping, applied to the rewrite's *output*: the signed-in person's display name and email, plus any token with five or more digits (MRNs, case numbers, phone numbers).

If the rewrite fails or comes back empty, nothing is sent: the raw message is never a fallback. Web search only runs when the person asks (the composer's globe button, or the trigger phrases), and every web answer stores and shows the exact query that left the machine (`metadata.web.query_sent`). Tests: `core/tests/test_web_search.py`, which uses the doctor's query from this flag.

Still open: rule-based stripping of *other* people's names pulled from the graph. It was left out because graph entities include topics ("Raft", "Neo4j"), and stripping every entity would gut ordinary queries. That needs person-typed entities first.

---
