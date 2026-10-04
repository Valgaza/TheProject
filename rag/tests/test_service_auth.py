"""Only core gets in -- the RAG half of memory/tests/test_service_auth.py.

The browser used to upload straight here with no user at all, which put every
file in one pool anyone could query. Now a call without core's token never
reaches a route, so nothing reaches the corpus except through core's scoping.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from starlette.testclient import TestClient

TOKEN = "core-and-rag-only"


@pytest.fixture
def http(monkeypatch):
    # rag_api.py is a script beside the package rather than part of it, so it
    # is loaded by path instead of importing through sys.path.
    spec = importlib.util.spec_from_file_location(
        "rag_api", Path(__file__).resolve().parent.parent / "rag_api.py"
    )
    rag_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rag_api)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    return TestClient(rag_api.app)


def test_a_query_without_the_token_is_refused(http):
    response = http.post("/api/query", json={"question": "anything", "user_id": "u"})
    assert response.status_code == 401


def test_a_delete_with_the_wrong_token_is_refused(http):
    response = http.delete("/api/documents/whatever", headers={"X-Service-Token": "guess"})
    assert response.status_code == 401


def test_health_stays_open_for_liveness_checks(http):
    assert http.get("/api/health").status_code == 200
