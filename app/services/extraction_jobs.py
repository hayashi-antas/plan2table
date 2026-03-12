"""Extraction job runners: run extractors and save metadata for each job kind."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.core.config import vision_service_account_json
from app.services.job_runner import csv_profile, csv_profile_no_header
from extractors.e055_extractor import extract_e055_pdf
from extractors.e142_extractor import extract_e142_pdf
from extractors.e251_extractor import extract_e251_pdf
from extractors.job_store import create_job, resolve_job_csv_path, save_metadata
from extractors.raster_extractor import extract_raster_pdf
from extractors.unified_csv import merge_vector_raster_csv
from extractors.vector_extractor import extract_vector_pdf_four_columns

if TYPE_CHECKING:
    from fastapi import UploadFile


def is_pdf_upload(file: "UploadFile", first_bytes: bytes | None = None) -> bool:
    """Return True if the upload looks like a PDF (filename, content_type, and optional magic)."""
    if first_bytes is not None and len(first_bytes) >= 5:
        if not first_bytes.startswith(b"%PDF"):
            return False
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        return False
    ct = (getattr(file, "content_type", None) or "").strip().lower()
    if ct and not ct.startswith("application/pdf") and ct != "application/octet-stream":
        return False
    return True


def is_parallel_extract_enabled() -> bool:
    raw = os.getenv("ME_CHECK_PARALLEL_EXTRACT", "1").strip().lower()
    return raw not in {"0", "false"}


def run_e055_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="e055", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "e055.csv"
    debug_dir = job.job_dir / "debug" / job.job_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    extract_result = extract_e055_pdf(
        pdf_path=input_pdf_path,
        out_csv=csv_path,
        debug_dir=debug_dir,
        vision_service_account_json=vision_service_account_json,
        page=0,
        dpi=300,
        y_cluster=18.0,
    )
    profile = csv_profile(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["e055.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "e055-v1",
            "extract_result": extract_result,
        },
    )
    return job, profile


def run_e251_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="e251", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "e251.csv"
    debug_dir = job.job_dir / "debug" / job.job_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    extract_result = extract_e251_pdf(
        pdf_path=input_pdf_path,
        out_csv=csv_path,
        debug_dir=debug_dir,
        vision_service_account_json=vision_service_account_json,
        page=0,
        dpi=300,
        y_cluster=14.0,
    )
    profile = csv_profile(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["e251.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "e251-v1",
            "extract_result": extract_result,
        },
    )
    return job, profile


def run_e142_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="e142", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "e142.csv"
    debug_dir = Path(job.job_dir) / "debug" / job.job_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    extract_result = extract_e142_pdf(
        pdf_path=input_pdf_path,
        out_csv=csv_path,
        debug_dir=debug_dir,
        vision_service_account_json=vision_service_account_json,
        page=0,
        dpi=300,
        y_cluster=12.0,
        x_gap=70.0,
    )
    profile = csv_profile_no_header(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["e142.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "e142-v1",
            "extract_result": extract_result,
        },
    )
    return job, profile


def run_raster_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="raster", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "raster.csv"
    debug_dir = job.job_dir / "debug"

    extract_result = extract_raster_pdf(
        pdf_path=input_pdf_path,
        out_csv=csv_path,
        debug_dir=debug_dir,
        vision_service_account_json=vision_service_account_json,
        page=0,
        dpi=300,
        y_cluster=20.0,
    )
    profile = csv_profile(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["raster.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "raster-v3",
            "extract_result": extract_result,
        },
    )
    return job, profile


def run_vector_job(file_bytes: bytes, source_filename: str):
    job = create_job(kind="vector", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "vector.csv"

    extract_result = extract_vector_pdf_four_columns(
        pdf_path=input_pdf_path,
        out_csv_path=csv_path,
    )
    profile = csv_profile(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["vector.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "vector-v1",
            "extract_result": extract_result,
        },
    )
    return job, profile


def resolve_existing_csv_or_404(job_id: str, kind: str) -> Path:
    try:
        csv_path = resolve_job_csv_path(job_id=job_id, kind=kind)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.parent.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="CSV not found")
    return csv_path


def run_unified_job(raster_job_id: str, vector_job_id: str):
    raster_csv_path = resolve_existing_csv_or_404(job_id=raster_job_id, kind="raster")
    vector_csv_path = resolve_existing_csv_or_404(job_id=vector_job_id, kind="vector")

    source_name = f"merge:{raster_job_id}+{vector_job_id}"
    job = create_job(kind="unified", source_filename=source_name)
    out_csv_path = job.job_dir / "unified.csv"

    merge_result = merge_vector_raster_csv(
        vector_csv_path=vector_csv_path,
        raster_csv_path=raster_csv_path,
        out_csv_path=out_csv_path,
    )
    profile = csv_profile(out_csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["unified.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "unified-v2",
            "source_job_ids": {
                "raster_job_id": raster_job_id,
                "vector_job_id": vector_job_id,
            },
            "extract_result": merge_result,
        },
    )
    return job, profile
