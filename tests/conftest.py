"""Shared pytest fixtures for plan2table tests. For WordBox/Segment builders use tests.helpers."""

from __future__ import annotations

import pytest


@pytest.fixture
def tmp_job_root(tmp_path, monkeypatch):
    """Point job_store.JOBS_ROOT at the test tmp_path for the duration of the test."""
    from extractors import job_store

    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    return tmp_path
