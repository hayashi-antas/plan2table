"""Shared pytest fixtures and helpers for plan2table tests."""

from __future__ import annotations

import pytest

# WordBox from common (single source for extractors).
from extractors.common import WordBox
from extractors.e142_extractor import Segment


def word_box(
    text: str,
    cx: float,
    cy: float = 100.0,
    w: float = 10.0,
    h: float = 10.0,
) -> WordBox:
    """Build a WordBox for tests. Defaults give a small bbox around (cx, cy)."""
    half_w = w / 2.0
    half_h = h / 2.0
    return WordBox(
        text=text,
        cx=cx,
        cy=cy,
        bbox=(cx - half_w, cy - half_h, cx + half_w, cy + half_h),
    )


# Aliases used by existing tests.
_wb = word_box
_word = word_box


def segment(
    text: str,
    *,
    y: float,
    x0: float,
    x1: float,
    page: int = 1,
) -> Segment:
    """Build a Segment for e142 tests."""
    compact = text.replace(" ", "").replace("　", "")
    return Segment(
        page=page,
        row_y=y,
        x0=x0,
        x1=x1,
        top=y - 6.0,
        bottom=y + 6.0,
        text=text,
        text_compact=compact,
    )


_segment = segment


@pytest.fixture
def tmp_job_root(tmp_path, monkeypatch):
    """Point job_store.JOBS_ROOT at the test tmp_path for the duration of the test."""
    from extractors import job_store

    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    return tmp_path
