"""CSV profile helpers and shared job-run utilities."""

from __future__ import annotations

import csv
from pathlib import Path


def csv_profile(csv_path: Path) -> dict:
    """Return row count (excluding header) and column names from a header CSV."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        columns = next(reader, None)
        if columns is None:
            return {"rows": 0, "columns": []}
        count = sum(1 for _ in reader)
    return {"rows": max(0, count), "columns": columns}


def csv_profile_no_header(csv_path: Path) -> dict:
    """Return row count and generic column labels for a headerless CSV."""
    count = 0
    max_columns = 0
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            count += 1
            max_columns = max(max_columns, len(row))
    return {
        "rows": count,
        "columns": [f"column_{i + 1}" for i in range(max_columns)],
    }
