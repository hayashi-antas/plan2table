"""POST routes for individual extractors: E-055, E-251, E-142."""

import asyncio
import logging

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse

from app.core.config import vision_service_account_json
from app.core.renderers import (
    build_e142_rows_html,
    build_equipment_table_html,
    render_extractor_error_html,
    render_extractor_success_html,
)
from app.services.extraction_jobs import (
    is_pdf_upload,
    run_e055_job,
    run_e142_job,
    run_e251_job,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/e-055/upload", response_class=HTMLResponse)
async def handle_e055_upload(file: UploadFile = File(...)):
    if not vision_service_account_json:
        return render_extractor_error_html(
            "e055", "VISION_SERVICE_ACCOUNT_KEY is not configured."
        )

    try:
        file_bytes = await file.read()
        if not is_pdf_upload(file, first_bytes=file_bytes):
            return render_extractor_error_html(
                "e055", "Please upload a valid PDF file."
            )
        job, profile = await asyncio.to_thread(
            run_e055_job,
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        table_html = await asyncio.to_thread(
            build_equipment_table_html, job.job_dir / "e055.csv", "e055"
        )
        return await asyncio.to_thread(
            render_extractor_success_html,
            "e055",
            job.job_id,
            table_html,
            int(profile["rows"]),
        )
    except Exception:
        logger.exception("E-055 extraction failed")
        return render_extractor_error_html(
            "e055", "An internal error occurred while processing your request."
        )


@router.post("/e-251/upload", response_class=HTMLResponse)
async def handle_e251_upload(file: UploadFile = File(...)):
    if not vision_service_account_json:
        return render_extractor_error_html(
            "e251", "VISION_SERVICE_ACCOUNT_KEY is not configured."
        )

    try:
        file_bytes = await file.read()
        if not is_pdf_upload(file, first_bytes=file_bytes):
            return render_extractor_error_html(
                "e251", "Please upload a valid PDF file."
            )
        job, profile = await asyncio.to_thread(
            run_e251_job,
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        table_html = await asyncio.to_thread(
            build_equipment_table_html, job.job_dir / "e251.csv", "e251"
        )
        return await asyncio.to_thread(
            render_extractor_success_html,
            "e251",
            job.job_id,
            table_html,
            int(profile["rows"]),
        )
    except Exception:
        logger.exception("E-251 extraction failed")
        return render_extractor_error_html(
            "e251", "An internal error occurred while processing your request."
        )


@router.post("/e-142/upload", response_class=HTMLResponse)
async def handle_e142_upload(file: UploadFile = File(...)):
    if not vision_service_account_json:
        return render_extractor_error_html(
            "e142", "VISION_SERVICE_ACCOUNT_KEY is not configured."
        )

    try:
        file_bytes = await file.read()
        if not is_pdf_upload(file, first_bytes=file_bytes):
            return render_extractor_error_html(
                "e142", "Please upload a valid PDF file."
            )
        job, profile = await asyncio.to_thread(
            run_e142_job,
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        rows_html = await asyncio.to_thread(
            build_e142_rows_html,
            job.job_dir / "e142.csv",
        )
        return render_extractor_success_html(
            "e142", job.job_id, rows_html, int(profile["rows"])
        )
    except Exception:
        logger.exception("E-142 extraction failed")
        return render_extractor_error_html(
            "e142", "An internal error occurred while processing your request."
        )
