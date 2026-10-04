"""Uploading documents: what gets recorded, and what happens when it fails.

The recurring theme is that two stores have to stay in step without a
transaction spanning them. Most of these tests are about the seam -- what the
person sees when one side works and the other doesn't.
"""

from __future__ import annotations

import uuid

import pytest


class FakeRag:
    """Stands in for the RAG service.

    Records what it was asked to do, because most of what matters here is
    whether ownership actually reached the ingest call -- an unowned chunk is
    invisible to every scoped read, and nothing about that failure is loud.
    """

    def __init__(self, *, chunks: int = 5, boom: bool = False, errors=None) -> None:
        self.chunks = chunks
        self.boom = boom
        self.errors = errors or []
        self.ingested: list[dict] = []
        self.deleted: list[uuid.UUID] = []

    async def ingest(self, **kwargs):
        self.ingested.append(kwargs)
        if self.boom:
            raise RuntimeError("rag is down")
        return {"points_upserted": self.chunks, "errors": self.errors}

    async def delete_document(self, document_id):
        self.deleted.append(document_id)
        return self.chunks

    async def excerpt(self, document_id, chars=4000):
        return "some opening text"

    async def has_documents(self, user_id, project_id):
        return True

    async def query(self, question, *, user_id, project_id):
        return None


def _md(name: str = "notes.md", body: bytes = b"# Notes\n\nSome content.") -> dict:
    return {"file": (name, body, "text/markdown")}


@pytest.fixture
def rag(db_client):
    client = FakeRag()
    db_client._transport.app.state.rag = client
    return client


# ── uploading ─────────────────────────────────────────────────────────────


async def test_a_loose_upload_is_recorded_in_the_universal_tier(db_client, rag):
    response = await db_client.post("/documents", files=_md())

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] is None
    assert body["status"] == "ingested"
    assert body["chunk_count"] == 5
    assert body["file_name"] == "notes.md"


async def test_a_project_upload_carries_its_project_into_the_vector_store(db_client, rag):
    """The id has to reach the ingest call, not just the Postgres row. If it
    stops here, the chunks are stored unscoped and project isolation is
    silently gone while the file list still looks right."""
    project = (await db_client.post("/projects", json={"name": "Thesis"})).json()

    await db_client.post(
        "/documents", params={"project_id": project["id"]}, files=_md()
    )

    assert str(rag.ingested[0]["project_id"]) == project["id"]


async def test_a_loose_upload_sends_no_project_to_the_vector_store(db_client, rag):
    await db_client.post("/documents", files=_md())
    assert rag.ingested[0]["project_id"] is None


async def test_the_record_and_the_chunks_share_one_id(db_client, rag):
    """The only link between the two stores. Without it a deleted row leaves
    chunks nothing can find, still answering questions."""
    body = (await db_client.post("/documents", files=_md())).json()
    assert str(rag.ingested[0]["document_id"]) == body["id"]


async def test_uploading_into_someone_elses_project_is_refused(db_client, rag):
    response = await db_client.post(
        "/documents", params={"project_id": str(uuid.uuid4())}, files=_md()
    )
    assert response.status_code == 404
    assert rag.ingested == []


# ── when ingest fails ─────────────────────────────────────────────────────


async def test_a_failed_ingest_leaves_a_record_saying_so(db_client):
    """Not a 500 and not a vanished file. Something the person can see in
    their list, understand, and retry."""
    db_client._transport.app.state.rag = FakeRag(boom=True)

    response = await db_client.post("/documents", files=_md())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "rag is down" in body["error"]
    assert body["chunk_count"] == 0


async def test_a_document_that_produced_no_chunks_is_a_failure(db_client):
    """An empty PDF ingests without erroring and retrieves nothing forever.
    Zero chunks is the only signal that happened."""
    db_client._transport.app.state.rag = FakeRag(chunks=0)

    body = (await db_client.post("/documents", files=_md())).json()

    assert body["status"] == "failed"
    assert "nothing could be read" in body["error"]


async def test_errors_reported_by_the_pipeline_reach_the_record(db_client):
    db_client._transport.app.state.rag = FakeRag(chunks=2, errors=["page 4 is not text"])

    body = (await db_client.post("/documents", files=_md())).json()

    assert body["status"] == "failed"
    assert "page 4 is not text" in body["error"]


async def test_an_unreadable_file_type_is_refused_before_it_is_read(db_client, rag):
    response = await db_client.post(
        "/documents", files={"file": ("cat.png", b"\x89PNG", "image/png")}
    )
    assert response.status_code == 400
    assert "png" in response.json()["detail"]
    assert rag.ingested == []


async def test_an_empty_file_is_refused(db_client, rag):
    response = await db_client.post("/documents", files=_md(body=b""))
    assert response.status_code == 400
    assert rag.ingested == []


# ── listing ───────────────────────────────────────────────────────────────


async def test_listing_separates_a_project_from_the_universal_tier(db_client, rag):
    project = (await db_client.post("/projects", json={"name": "Thesis"})).json()
    await db_client.post("/documents", files=_md("loose.md"))
    await db_client.post(
        "/documents", params={"project_id": project["id"]}, files=_md("filed.md")
    )

    everything = (await db_client.get("/documents")).json()
    loose = (await db_client.get("/documents", params={"loose": True})).json()
    filed = (
        await db_client.get("/documents", params={"project_id": project["id"]})
    ).json()

    # Assert about these two files, not about the whole table. The demo user
    # is a real account that real usage writes to, so a test demanding an
    # empty database passes only until someone uses the app.
    assert {"loose.md", "filed.md"} <= {d["file_name"] for d in everything}
    assert "loose.md" in {d["file_name"] for d in loose}
    assert "filed.md" not in {d["file_name"] for d in loose}
    assert [d["file_name"] for d in filed] == ["filed.md"]


async def test_a_new_upload_has_not_reached_the_graph_yet(db_client, rag):
    """graph_synced_at is stamped by the background task, so it is null on
    the response. Null always means "the graph does not know about this",
    never "we are not sure"."""
    body = (await db_client.post("/documents", files=_md())).json()
    assert body["graph_synced_at"] is None


# ── deleting ──────────────────────────────────────────────────────────────


async def test_deleting_removes_the_chunks_and_then_the_record(db_client, rag):
    body = (await db_client.post("/documents", files=_md())).json()

    response = await db_client.delete(f"/documents/{body['id']}")

    assert response.status_code == 204
    assert [str(d) for d in rag.deleted] == [body["id"]]
    remaining = {d["id"] for d in (await db_client.get("/documents")).json()}
    assert body["id"] not in remaining


async def test_deleting_someone_elses_document_is_refused(db_client, rag):
    response = await db_client.delete(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404
    assert rag.deleted == []
