"""Which memory a fact belongs to.

Two tiers. A **user** scope holds what stays true across everything the person
does -- who they are, what they prefer, standing facts. A **project** scope holds
what is only true of one body of work. A read unions both; a write picks one.

That asymmetry is not a preference, it is what graphiti 0.29 allows. Every read
path (`search`, `search_`, `retrieve_episodes`, `get_by_group_ids`) takes
``group_ids: list[str]`` and compiles to a single ``WHERE group_id IN $ids``
with unified ranking. ``add_episode`` takes exactly one ``group_id``, and its
prior-episode context window is confined to that group. So the write gate in
phase 5 has to *choose* a tier rather than write to both.

Group ids are opaque to graphiti beyond ``validate_group_id``, which permits
``[a-zA-Z0-9_-]+``. The scheme here is:

    user:     u_<32 hex>
    project:  p_<32 hex>_<32 hex>          (user, then project)

Fixed width, because the previous scheme was ``f"{user_id}_{project_id}"`` and
collided: user "a_b" with no project produced the same id as user "a" in project
"b". UUID hex is always 32 characters, so the parts cannot run together. The
prefix is there so a group id read off a Neo4j node says what it is without
needing to be parsed.
"""

from __future__ import annotations

import uuid
from typing import Literal

Scope = Literal["user", "project"]

USER_PREFIX = "u"
PROJECT_PREFIX = "p"


def user_group(user_id: uuid.UUID) -> str:
    """The group holding everything durable about one person."""
    return f"{USER_PREFIX}_{user_id.hex}"


def project_group(user_id: uuid.UUID, project_id: uuid.UUID) -> str:
    """The group holding one project's memory.

    Carries the user id as well as the project id so a project group can be
    identified as belonging to a person without a second lookup, and so two
    users' projects can never share a group even if ids were ever reissued.
    """
    return f"{PROJECT_PREFIX}_{user_id.hex}_{project_id.hex}"


def write_group(scope: Scope, user_id: uuid.UUID, project_id: uuid.UUID | None) -> str:
    """The single group an episode is written to.

    Raises rather than silently demoting to user scope: a project write that
    quietly lands in the user's durable memory is exactly the bug that would be
    hardest to notice later.
    """
    if scope == "user":
        return user_group(user_id)
    if project_id is None:
        raise ValueError("project scope requires a project_id")
    return project_group(user_id, project_id)


def read_groups(user_id: uuid.UUID, project_id: uuid.UUID | None) -> list[str]:
    """Every group a read should union, broadest first.

    Reading from a project always includes the user tier, because facts about
    the person hold inside their projects. The reverse is not true, which is
    what keeps two projects from leaking into each other.
    """
    groups = [user_group(user_id)]
    if project_id is not None:
        groups.append(project_group(user_id, project_id))
    return groups


def scope_of(group_id: str) -> Scope:
    """Which tier a group id belongs to.

    Lets a search result say where a fact came from without the caller having to
    hold onto which group it asked for.
    """
    if group_id.startswith(f"{USER_PREFIX}_"):
        return "user"
    if group_id.startswith(f"{PROJECT_PREFIX}_"):
        return "project"
    raise ValueError(f"unrecognised group id: {group_id!r}")


View = Literal["all", "user", "project"]


def view_groups(view: View, user_id: uuid.UUID, project_id: uuid.UUID | None) -> list[str]:
    """The groups one *view* of the graph covers.

    ``read_groups`` answers a different question -- what a retrieval in this
    context is allowed to see -- and that is always the union. A view is for
    looking: "user" is the universal graph on its own, "project" is one body of
    work on its own, and "all" is the union, which is what a read would return.

    Keeping them as separate functions matters. A project view that quietly
    included the user tier would make the two tiers impossible to tell apart on
    screen, and telling them apart is the entire point of having two.
    """
    if view == "user":
        return [user_group(user_id)]
    if view == "project":
        if project_id is None:
            raise ValueError("the project view requires a project_id")
        return [project_group(user_id, project_id)]
    return read_groups(user_id, project_id)
