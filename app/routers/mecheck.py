"""POST routes for M-E-Check: customer run, raster/vector upload, unified merge."""

import asyncio
import html
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from app.core.config import vision_service_account_json
from app.core.renderers import render_job_result_html
from app.core.utils import exception_message
from app.services.customer_table import (
    build_customer_table_html,
    render_customer_error_html,
    render_customer_success_html,
)
from app.services.extraction_jobs import (
    is_parallel_extract_enabled,
    is_pdf_upload,
    run_raster_job,
    run_unified_job,
    run_vector_job,
)

router = APIRouter()


@router.post("/customer/run", response_class=HTMLResponse)
async def handle_customer_run(
    panel_file: UploadFile = File(...),
    equipment_file: UploadFile = File(...),
):
    if not is_pdf_upload(panel_file):
        return render_customer_error_html(
            stage="panel->raster",
            message="Please upload a valid PDF file for panel_file.",
        )
    if not is_pdf_upload(equipment_file):
        return render_customer_error_html(
            stage="equipment->vector",
            message="Please upload a valid PDF file for equipment_file.",
        )

    panel_file_bytes = await panel_file.read()
    equipment_file_bytes = await equipment_file.read()
    parallel_extract_enabled = is_parallel_extract_enabled()
    raster_profile: Optional[dict] = None
    vector_profile: Optional[dict] = None

    if parallel_extract_enabled:
        raster_result, vector_result = await asyncio.gather(
            asyncio.to_thread(
                run_raster_job,
                file_bytes=panel_file_bytes,
                source_filename=panel_file.filename or "panel.pdf",
            ),
            asyncio.to_thread(
                run_vector_job,
                file_bytes=equipment_file_bytes,
                source_filename=equipment_file.filename or "equipment.pdf",
            ),
            return_exceptions=True,
        )
        raster_exc = raster_result if isinstance(raster_result, BaseException) else None
        vector_exc = vector_result if isinstance(vector_result, BaseException) else None

        if isinstance(raster_exc, asyncio.CancelledError):
            raise raster_exc
        if isinstance(vector_exc, asyncio.CancelledError):
            raise vector_exc

        if raster_exc and vector_exc:
            print(f"Customer flow failed at panel->raster: {raster_exc}")
            print(f"Customer flow failed at equipment->vector: {vector_exc}")
            return render_customer_error_html(
                stage="panel->raster",
                message=exception_message(raster_exc),
            )
        if raster_exc:
            print(f"Customer flow failed at panel->raster: {raster_exc}")
            return render_customer_error_html(
                stage="panel->raster",
                message=exception_message(raster_exc),
            )
        if vector_exc:
            print(f"Customer flow failed at equipment->vector: {vector_exc}")
            return render_customer_error_html(
                stage="equipment->vector",
                message=exception_message(vector_exc),
            )
        raster_job, raster_profile = raster_result
        vector_job, vector_profile = vector_result
    else:
        try:
            raster_job, raster_profile = await asyncio.to_thread(
                run_raster_job,
                file_bytes=panel_file_bytes,
                source_filename=panel_file.filename or "panel.pdf",
            )
        except Exception as exc:
            print(f"Customer flow failed at panel->raster: {exc}")
            return render_customer_error_html(
                stage="panel->raster",
                message=exception_message(exc),
            )

        try:
            vector_job, vector_profile = await asyncio.to_thread(
                run_vector_job,
                file_bytes=equipment_file_bytes,
                source_filename=equipment_file.filename or "equipment.pdf",
            )
        except Exception as exc:
            print(f"Customer flow failed at equipment->vector: {exc}")
            return render_customer_error_html(
                stage="equipment->vector",
                message=exception_message(exc),
            )

    try:
        unified_job, _ = await asyncio.to_thread(
            run_unified_job,
            raster_job_id=raster_job.job_id,
            vector_job_id=vector_job.job_id,
        )
        unified_csv_path = unified_job.job_dir / "unified.csv"
        table_html = build_customer_table_html(
            unified_csv_path,
            vector_row_count=int((vector_profile or {}).get("rows", 0)),
            raster_row_count=int((raster_profile or {}).get("rows", 0)),
        )
        return render_customer_success_html(
            unified_job_id=unified_job.job_id,
            table_html=table_html,
        )
    except Exception as exc:
        print(f"Customer flow failed at unified: {exc}")
        return render_customer_error_html(
            stage="unified",
            message=exception_message(exc),
        )


@router.post("/raster/upload", response_class=HTMLResponse)
async def handle_raster_upload(file: UploadFile = File(...)):
    if not is_pdf_upload(file):
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """
    if not vision_service_account_json:
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> VISION_SERVICE_ACCOUNT_KEY is not configured.
        </div>
        """

    try:
        file_bytes = await file.read()
        job, profile = run_raster_job(
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        return render_job_result_html(
            kind="raster",
            job_id=job.job_id,
            rows=int(profile["rows"]),
            columns=list(profile["columns"]),
        )
    except Exception as exc:
        print(f"Raster extraction failed: {exc}")
        safe_error = html.escape(str(exc), quote=True)
        return f"""
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error Processing Raster PDF:</strong><br>
            {safe_error}
        </div>
        """


@router.post("/vector/upload", response_class=HTMLResponse)
async def handle_vector_upload(file: UploadFile = File(...)):
    if not is_pdf_upload(file):
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """

    try:
        file_bytes = await file.read()
        job, profile = run_vector_job(
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        return render_job_result_html(
            kind="vector",
            job_id=job.job_id,
            rows=int(profile["rows"]),
            columns=list(profile["columns"]),
        )
    except Exception as exc:
        print(f"Vector extraction failed: {exc}")
        safe_error = html.escape(str(exc), quote=True)
        return f"""
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error Processing Vector PDF:</strong><br>
            {safe_error}
        </div>
        """


@router.post("/unified/merge", response_class=HTMLResponse)
async def handle_unified_merge(
    raster_job_id: UUID = Form(...),
    vector_job_id: UUID = Form(...),
):
    raster_job_id_str = str(raster_job_id)
    vector_job_id_str = str(vector_job_id)

    try:
        job, profile = run_unified_job(
            raster_job_id=raster_job_id_str,
            vector_job_id=vector_job_id_str,
        )
        return render_job_result_html(
            kind="unified",
            job_id=job.job_id,
            rows=int(profile["rows"]),
            columns=list(profile["columns"]),
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(f"Unified merge failed: {exc}")
        safe_error = html.escape(str(exc), quote=True)
        return f"""
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error Processing Unified CSV:</strong><br>
            {safe_error}
        </div>
        """
