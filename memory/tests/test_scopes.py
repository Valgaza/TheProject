"""The scope model: what a write picks, and what a read unions."""

from __future__ import annotations

import re
import uuid

import pytest

from nexus_memory.scopes import (
    project_group,
    read_groups,
    user_group,
    view_groups,
    write_group,
)

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")
THESIS = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SIDE = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

# graphiti rejects anything outside this set in validate_group_id.
GRAPHITI_ALLOWED = re.compile(r"^[a-zA-Z0-9_-]+$")


@pytest.mark.parametrize(
    "group",
    [user_group(ALICE), project_group(ALICE, THESIS)],
)
def test_group_ids_are_acceptable_to_graphiti(group):
    assert GRAPHITI_ALLOWED.match(group)


def test_the_old_scheme_could_collide_and_this_one_cannot():
    """The point of fixed-width parts.

    ``f"{user}_{project}"`` meant user "a_b" with no project produced the same
    id as user "a" in project "b". UUID hex is always 32 characters, so the
    parts can never run together ambiguously.
    """
    user_only = user_group(ALICE)
    with_project = project_group(ALICE, THESIS)

    assert user_only != with_project
    assert not with_project.startswith(user_only)
    assert len(user_only) == len("u_") + 32
    assert len(with_project) == len("p_") + 32 + 1 + 32


def test_projects_of_different_users_never_share_a_group():
    assert project_group(ALICE, THESIS) != project_group(BOB, THESIS)


def test_a_users_projects_are_distinct_from_each_other():
    assert project_group(ALICE, THESIS) != project_group(ALICE, SIDE)


def test_a_write_lands_in_exactly_one_group():
    assert write_group("user", ALICE, THESIS) == user_group(ALICE)
    assert write_group("project", ALICE, THESIS) == project_group(ALICE, THESIS)


def test_a_project_write_without_a_project_is_refused():
    """Silently demoting to user scope would be the hardest bug to notice:
    project facts would quietly pollute durable memory."""
    with pytest.raises(ValueError):
        write_group("project", ALICE, None)


def test_a_read_unions_both_tiers():
    groups = read_groups(ALICE, THESIS)

    assert groups == [user_group(ALICE), project_group(ALICE, THESIS)]


def test_a_read_without_a_project_sees_only_the_user_tier():
    assert read_groups(ALICE, None) == [user_group(ALICE)]


def test_one_project_cannot_read_another():
    """The asymmetry that makes two-tier scoping worth having: user facts reach
    into every project, project facts stay put."""
    thesis_read = set(read_groups(ALICE, THESIS))
    side_read = set(read_groups(ALICE, SIDE))

    assert project_group(ALICE, SIDE) not in thesis_read
    assert project_group(ALICE, THESIS) not in side_read
    assert thesis_read & side_read == {user_group(ALICE)}


# ── views ─────────────────────────────────────────────────────────────────────
# A view is what the panel draws, which is not the same question as what a read
# is allowed to see. Reads always union; views can isolate a tier.


def test_the_user_view_is_the_universal_tier_alone():
    assert view_groups("user", ALICE, THESIS) == [user_group(ALICE)]


def test_the_project_view_excludes_the_user_tier():
    """Otherwise the two tiers are indistinguishable on screen."""
    groups = view_groups("project", ALICE, THESIS)

    assert groups == [project_group(ALICE, THESIS)]
    assert user_group(ALICE) not in groups


def test_the_default_view_is_what_a_read_would_see():
    assert view_groups("all", ALICE, THESIS) == read_groups(ALICE, THESIS)


def test_a_project_view_without_a_project_is_refused():
    with pytest.raises(ValueError):
        view_groups("project", ALICE, None)
