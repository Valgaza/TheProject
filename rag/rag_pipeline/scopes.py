"""Which tier a document belongs to, and which tiers a question may read.

This is deliberately the same shape as ``memory/nexus_memory/scopes.py``. The
graph and the corpus answer the same question -- "what is this person allowed
to recall here?" -- and answering it two different ways is how the two tiers
drift apart until a project's documents start showing up somewhere they should
not.

The convention, matching memory's group ids:

    user tier      "user:<user_id>"          visible in every chat
    project tier   "project:<user_id>:<id>"  visible only inside that project

Chunks carry their key in the Qdrant payload as ``scope_key``. A read passes
the list of keys it is allowed to see, and Qdrant's MatchAny turns that list
into an OR -- so ``read_scopes`` is the whole access rule, in one function,
the same way ``read_groups`` is for the graph.
"""

from __future__ import annotations

USER_TIER = "user"
PROJECT_TIER = "project"


def user_scope(user_id: str) -> str:
    """The universal tier for one person."""
    return f"{USER_TIER}:{user_id}"


def project_scope(user_id: str, project_id: str) -> str:
    """One project's own tier.

    Keyed by user as well as project so that two people's projects can never
    collide on an id, even though nothing today creates that situation.
    """
    return f"{PROJECT_TIER}:{user_id}:{project_id}"


def write_scope(user_id: str, project_id: str | None) -> str:
    """The single tier a document is written to.

    No project means the universal tier. Unlike memory's ``write_group`` this
    cannot raise: a document with no project is a legitimate universal upload,
    whereas a *fact* claiming project scope with no project to put it in is a
    contradiction worth refusing.
    """
    if project_id is None:
        return user_scope(user_id)
    return project_scope(user_id, project_id)


def read_scopes(user_id: str, project_id: str | None) -> list[str]:
    """Every tier a question asked here may retrieve from, broadest first.

    A question inside a project reads that project's documents *and* the
    universal ones, because a reference the person uploaded for themselves
    holds inside their projects. The reverse is not true, and that asymmetry
    is what stops one project's corpus answering another's questions.

    Identical in spirit to ``memory.scopes.read_groups``. If you change one,
    change the other, or a fact and the document it came from will stop being
    visible in the same places.
    """
    scopes = [user_scope(user_id)]
    if project_id is not None:
        scopes.append(project_scope(user_id, project_id))
    return scopes
