"""When the graph gets tidied, and what gets written down about it.

The sweep itself lives in the memory service and is tested there. This covers
the half that lives here: the record, the tier scoping, and the counter that
decides when a pass runs without being asked.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from nexus_core.models import MaintenanceRun
from nexus_core.routers.maintenance import EPISODES_BETWEEN_SWEEPS, maybe_sweep

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")


# The group id the fake reports. Real sweeps mint one from the uuid scheme, so
# filtering on this keeps assertions clear of rows a live service wrote -- the
# rollback fixture isolates what a test writes, not what is already committed.
FAKE_GROUP = "g-fake-for-tests"


class FakeMemory:
    """Stands in for the memory service, recording what it was asked to sweep."""

    def __init__(self, *, clean: bool = True) -> None:
        self.calls: list[dict] = []
        self.clean = clean

    async def maintenance(self, *, user_id, project_id, view, dry_run):
        self.calls.append(
            {"project_id": project_id, "view": view, "dry_run": dry_run}
        )
        scope = "project" if view == "project" else "user"
        return {
            "group_ids": [FAKE_GROUP],
            "dry_run": dry_run,
            "clean": self.clean,
            "total_changes": 0 if self.clean else 3,
            "reports": [
                {
                    "group_id": FAKE_GROUP,
                    "scope": scope,
                    "duplicate_edges": [],
                    "near_duplicates": [],
                    "conflicts": [],
                    "suspect_invalidations": [],
                    "summaries_rebuilt": [] if self.clean else ["Neo4j"],
                    "episodes_suppressed": [],
                    "orphans_deleted": [],
                }
            ],
        }


async def _with_memory(db_client, memory):
    from nexus_core.main import app

    app.state.memory = memory
    return app


# ── On demand ─────────────────────────────────────────────────────────────────


async def test_a_manual_run_defaults_to_reporting_only(db_client, session_factory):
    """This pass will be wrong sometimes; the diff is worth reading first."""
    memory = FakeMemory()
    await _with_memory(db_client, memory)

    response = await db_client.post("/maintenance", json={})

    assert response.status_code == 200
    assert memory.calls[0]["dry_run"] is True


async def test_a_run_is_written_down(db_client, session_factory):
    memory = FakeMemory(clean=False)
    await _with_memory(db_client, memory)

    await db_client.post("/maintenance", json={"dry_run": False})

    async with session_factory() as session:
        runs = (
            await session.execute(
                select(MaintenanceRun).where(MaintenanceRun.group_id == FAKE_GROUP)
            )
        ).scalars().all()

    assert len(runs) == 1
    assert runs[0].dry_run is False
    assert runs[0].report["summaries_rebuilt"] == ["Neo4j"]
    assert runs[0].finished_at is not None


async def test_a_project_view_needs_a_project(db_client):
    await _with_memory(db_client, FakeMemory())

    response = await db_client.post("/maintenance", json={"view": "project"})

    assert response.status_code == 422


async def test_an_unknown_project_is_refused(db_client):
    await _with_memory(db_client, FakeMemory())

    response = await db_client.post(
        "/maintenance", json={"view": "project", "project_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404


async def test_a_user_tier_run_is_not_filed_under_a_project(db_client, session_factory):
    """A sweep of the universal tier has no project even when it was launched
    from inside one -- filing it under the project would make the counter for
    that project think it had been swept."""
    memory = FakeMemory()
    await _with_memory(db_client, memory)
    project = (await db_client.post("/projects", json={"name": "Thesis"})).json()

    await db_client.post(
        "/maintenance",
        json={"view": "user", "project_id": project["id"], "dry_run": False},
    )

    async with session_factory() as session:
        run = (
            await session.execute(
                select(MaintenanceRun).where(MaintenanceRun.group_id == FAKE_GROUP)
            )
        ).scalars().one()

    assert run.scope == "user"
    assert run.project_id is None


# ── The automatic trigger ─────────────────────────────────────────────────────


async def test_a_quiet_tier_is_not_swept(db_client, session_factory):
    """A counter, not a timer: the graph only changes when you talk to it."""
    memory = FakeMemory()

    await maybe_sweep(
        session_factory=session_factory,
        memory=memory,
        user_id=USER,
        project_id=None,
        scope="user",
    )

    assert memory.calls == []


async def test_enough_new_episodes_triggers_a_real_sweep(db_client, session_factory):
    memory = FakeMemory()
    project_id, chat_id = await _seed_episodes(
        db_client, session_factory, count=EPISODES_BETWEEN_SWEEPS
    )

    await maybe_sweep(
        session_factory=session_factory,
        memory=memory,
        user_id=USER,
        project_id=uuid.UUID(project_id),
        scope="project",
    )

    assert len(memory.calls) == 1
    # Automatic runs apply. A dry run that nobody reads is just latency.
    assert memory.calls[0]["dry_run"] is False


async def test_the_counter_resets_after_a_run(db_client, session_factory):
    """Otherwise every turn past the threshold sweeps again forever."""
    memory = FakeMemory()
    project_id, _ = await _seed_episodes(
        db_client, session_factory, count=EPISODES_BETWEEN_SWEEPS
    )

    for _ in range(3):
        await maybe_sweep(
            session_factory=session_factory,
            memory=memory,
            user_id=USER,
            project_id=uuid.UUID(project_id),
            scope="project",
        )

    assert len(memory.calls) == 1


async def test_a_sweep_failure_never_reaches_the_conversation(db_client, session_factory):
    """It runs behind a reply that has already been sent."""

    class BrokenMemory:
        async def maintenance(self, **_):
            raise RuntimeError("memory service is down")

    project_id, _ = await _seed_episodes(
        db_client, session_factory, count=EPISODES_BETWEEN_SWEEPS
    )

    await maybe_sweep(
        session_factory=session_factory,
        memory=BrokenMemory(),
        user_id=USER,
        project_id=uuid.UUID(project_id),
        scope="project",
    )


async def _seed_episodes(db_client, session_factory, *, count: int) -> tuple[str, str]:
    """A project whose graph has taken `count` ingested episodes."""
    from nexus_core.models import MemoryEpisode

    project = (await db_client.post("/projects", json={"name": "Swept"})).json()
    chat = (await db_client.post(f"/projects/{project['id']}/chats", json={})).json()

    async with session_factory() as session:
        for _ in range(count):
            session.add(
                MemoryEpisode(
                    chat_id=uuid.UUID(chat["id"]),
                    scope="project",
                    status="ingested",
                )
            )
        await session.commit()
    return project["id"], chat["id"]
