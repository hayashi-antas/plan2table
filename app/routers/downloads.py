"""GET routes for job CSV downloads."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from extractors.job_store import resolve_job_csv_path


def _download_job_csv(job_id: UUID, kind: str):
    job_id_str = str(job_id)
    try:
        csv_path = resolve_job_csv_path(job_id=job_id_str, kind=kind)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Job not found") from e
    if not csv_path.parent.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="CSV not found")
    download_filename = f"{kind}.csv"
    if kind == "unified":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        download_filename = f"me-check_照合結果_{timestamp}.csv"
    return FileResponse(
        path=csv_path,
        media_type="text/csv; charset=utf-8",
        filename=download_filename,
    )


router = APIRouter()


@router.get("/jobs/{job_id}/raster.csv")
async def download_raster_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="raster")


@router.get("/jobs/{job_id}/e055.csv")
async def download_e055_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="e055")


@router.get("/jobs/{job_id}/e251.csv")
async def download_e251_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="e251")


@router.get("/jobs/{job_id}/e142.csv")
async def download_e142_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="e142")


@router.get("/jobs/{job_id}/vector.csv")
async def download_vector_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="vector")


@router.get("/jobs/{job_id}/unified.csv")
async def download_unified_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="unified")
