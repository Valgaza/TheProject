"""Fixtures for the runtime tests.

Everything except test_runtime_mlx.py runs without MLX models: the scheduler
is exercised with fake jobs and the API with a fake backend, so the default
suite stays fast and needs no downloads.
"""

from __future__ import annotations

import os

import pytest

SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _service_token(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)
    os.environ.setdefault("SERVICE_TOKEN", SERVICE_TOKEN)
