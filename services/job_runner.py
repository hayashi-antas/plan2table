"""CSV profile helpers and shared job-run utilities."""

from __future__ import annotations

import csv
from pathlib import Path


def csv_profile(csv_path: Path) -> dict:
    """Return row count (excluding header) and column names from a header CSV."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return {"rows": 0, "columns": []}
    return {"rows": max(0, len(rows) - 1), "columns": rows[0]}


def csv_profile_no_header(csv_path: Path) -> dict:
    """Return row count and generic column labels for a headerless CSV."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    max_columns = max((len(row) for row in rows), default=0)
    return {
        "rows": len(rows),
        "columns": [f"column_{index + 1}" for index in range(max_columns)],
    }
