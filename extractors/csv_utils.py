"""CSV read helpers for extractors and app."""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv_dict_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read a CSV with header into a list of dicts (one per row)."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def read_csv_rows(csv_path: Path) -> list[list[str]]:
    """Read a CSV into a list of rows (each row is a list of cells)."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        return [list(row) for row in reader]
