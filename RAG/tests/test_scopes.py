"""Which tier a document lands in, and which tiers a question may read.

These are the isolation guarantees, stated as tests. They are cheap, they are
pure, and they are the first thing to run when someone changes how documents
are filtered -- the failure they protect against is silent, because a leaked
document does not error, it just answers a question it had no business
answering.

Deliberately mirrors the shape of the graph's own scope tests. If these two
files ever disagree, a fact and the document it came from have stopped being
visible in the same places.
"""

from __future__ import annotations

from rag_pipeline.scopes import (
    project_scope,
    read_scopes,
    user_scope,
    write_scope,
)

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
PROJECT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ── writing ───────────────────────────────────────────────────────────────


def test_a_document_with_no_project_lands_in_the_universal_tier():
    assert write_scope(ALICE, None) == user_scope(ALICE)


def test_a_document_uploaded_into_a_project_lands_in_that_project():
    assert write_scope(ALICE, PROJECT_A) == project_scope(ALICE, PROJECT_A)


def test_two_projects_never_share_a_write_scope():
    assert write_scope(ALICE, PROJECT_A) != write_scope(ALICE, PROJECT_B)


def test_two_people_never_share_a_scope_even_for_the_same_project_id():
    """Nothing creates this situation today. It costs one path segment to
    make it impossible rather than merely unlikely."""
    assert write_scope(ALICE, PROJECT_A) != write_scope(BOB, PROJECT_A)
    assert write_scope(ALICE, None) != write_scope(BOB, None)


# ── reading ───────────────────────────────────────────────────────────────


def test_a_loose_question_reads_the_universal_tier_only():
    assert read_scopes(ALICE, None) == [user_scope(ALICE)]


def test_a_project_question_reads_its_project_and_the_universal_tier():
    """A reference the person uploaded for themselves holds inside their
    projects, so a project question sees both."""
    scopes = read_scopes(ALICE, PROJECT_A)
    assert user_scope(ALICE) in scopes
    assert project_scope(ALICE, PROJECT_A) in scopes


def test_a_project_question_never_reads_another_project():
    """The guarantee. A document uploaded to project A must never be
    retrievable from project B."""
    scopes = read_scopes(ALICE, PROJECT_A)
    assert project_scope(ALICE, PROJECT_B) not in scopes


def test_a_loose_question_never_reads_any_project():
    """The asymmetry: reads union downward, never upward. A project's
    documents stay inside it, including from the user's own loose chats."""
    scopes = read_scopes(ALICE, None)
    assert project_scope(ALICE, PROJECT_A) not in scopes
    assert project_scope(ALICE, PROJECT_B) not in scopes


def test_a_question_never_reads_another_person():
    scopes = read_scopes(ALICE, PROJECT_A)
    assert not any(BOB in scope for scope in scopes)


def test_the_universal_tier_comes_first():
    """Broadest first, matching read_groups. Not load-bearing for filtering
    -- Qdrant treats the list as a set -- but it is what a human reading a
    log line expects, and the two functions should not diverge in cosmetics
    either."""
    assert read_scopes(ALICE, PROJECT_A)[0] == user_scope(ALICE)


def test_a_document_is_readable_from_exactly_where_it_was_written():
    """Ties the two halves together: whatever write_scope produces must
    appear in read_scopes for the same tier, or a document would be stored
    somewhere nothing can retrieve it from."""
    for project in (None, PROJECT_A, PROJECT_B):
        assert write_scope(ALICE, project) in read_scopes(ALICE, project)
