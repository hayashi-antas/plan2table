from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

JOBS_ROOT = Path("/tmp/plan2table/jobs")
RASTER_CSV_NAME = "raster.csv"
VECTOR_CSV_NAME = "vector.csv"
UNIFIED_CSV_NAME = "unified.csv"
E055_CSV_NAME = "e055.csv"
METADATA_NAME = "metadata.json"


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_dir: Path
    kind: str
    source_filename: str
    created_at: str


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_uuid_v4(job_id: str) -> None:
    parsed = UUID(job_id)
    if parsed.version != 4:
        raise ValueError("job_id must be UUID v4")


def create_job(kind: str, source_filename: str) -> JobContext:
    job_id = str(uuid4())
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    return JobContext(
        job_id=job_id,
        job_dir=job_dir,
        kind=kind,
        source_filename=source_filename,
        created_at=_utcnow_iso(),
    )


def fixed_csv_name(kind: str) -> str:
    if kind == "raster":
        return RASTER_CSV_NAME
    if kind == "vector":
        return VECTOR_CSV_NAME
    if kind == "unified":
        return UNIFIED_CSV_NAME
    if kind == "e055":
        return E055_CSV_NAME
    raise ValueError(f"Unsupported job kind: {kind}")


def save_csv(job: JobContext, csv_bytes: bytes) -> Path:
    csv_path = job.job_dir / fixed_csv_name(job.kind)
    csv_path.write_bytes(csv_bytes)
    return csv_path


def metadata_path(job: JobContext) -> Path:
    return job.job_dir / METADATA_NAME


def save_metadata(job: JobContext, metadata: dict[str, Any]) -> Path:
    payload = {
        "job_id": job.job_id,
        "kind": job.kind,
        "source_filename": job.source_filename,
        "created_at": job.created_at,
        **metadata,
    }
    path = metadata_path(job)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def resolve_job_csv_path(job_id: str, kind: str) -> Path:
    _validate_uuid_v4(job_id)
    csv_name = fixed_csv_name(kind)
    return JOBS_ROOT / job_id / csv_name
