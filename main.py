import asyncio
import html
import json
import logging
import os
import unicodedata
from pathlib import Path
from typing import Optional
from uuid import UUID

import bleach
import markdown
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from google.genai import types

from config import (
    MODEL_NAME,
    genai_client,
    vision_service_account_json,
)
from prompts import load_prompt
from extractors.text_extractor import extract_text_from_pdf
from extractors.area_regex import extract_summary_areas
from extractors.tool_definitions import TOOLS, SKILL_REGISTRY
from extractors.raster_extractor import extract_raster_pdf
from extractors.vector_extractor import extract_vector_pdf_four_columns
from extractors.e055_extractor import extract_e055_pdf
from extractors.e251_extractor import extract_e251_pdf
from extractors.e142_extractor import extract_e142_pdf
from extractors.job_store import create_job, resolve_job_csv_path, save_metadata
from extractors.unified_csv import merge_vector_raster_csv
from extractors.csv_utils import read_csv_dict_rows
from services.job_runner import csv_profile, csv_profile_no_header
from routers import downloads, pages
from utils import exception_message as _exception_message_util, parse_float_or_none
from renderers import (
    build_debug_script,
    build_equipment_table_html,
    build_e142_rows_html,
    render_extractor_error_html,
    render_extractor_success_html,
    render_job_result_html,
    render_parse_error,
    single_line_message as _single_line_message_renderer,
    stringify_cell,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(pages.router)
app.include_router(downloads.router)
logger = logging.getLogger(__name__)


def _stringify_cell(value):
    """Backward-compat alias."""
    return stringify_cell(value)


def _extract_json(raw_text):
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None


def _is_empty_value(value):
    """Check if a value is considered empty (None, "", "-", or similar)."""
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        return v == "" or v == "-" or v == "－" or v == "—"
    return False


def _get_diagram_type(data):
    """Get the diagram type from the parsed data."""
    if not isinstance(data, dict):
        return "unknown"
    diagram_type = str(data.get("diagram_type") or "").strip().lower()
    if diagram_type in ("detailed", "simple"):
        return diagram_type
    return "unknown"


def _validate_room_areas(data):
    """Validate room_areas based on diagram type.

    For 'detailed' diagrams: Check if rooms have empty area_m2 when calculation is possible.
    For 'simple' diagrams: Only check if calculation/tatami exists but area_m2 is empty.

    Returns:
        tuple: (warnings, found_rooms, warning_rooms) where:
               - warnings is a list of warning messages
               - found_rooms is a list of all detected room names
               - warning_rooms is a list of room names with warnings
    """
    warnings = []
    found_rooms = []
    warning_rooms = []

    if not isinstance(data, dict):
        return warnings, found_rooms, warning_rooms

    diagram_type = _get_diagram_type(data)
    room_rows = data.get("data", {}).get("room_areas", [])
    if not isinstance(room_rows, list):
        return warnings, found_rooms, warning_rooms

    for row in room_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("room_name") or "").strip()
        if not name:
            continue

        found_rooms.append(name)
        area_m2 = row.get("area_m2")
        calculation = str(row.get("calculation") or "").strip()
        tatami = row.get("tatami")

        # area_m2が空欄かどうかを判定
        area_is_empty = _is_empty_value(area_m2)
        tatami_is_empty = _is_empty_value(tatami)

        if not area_is_empty:
            # area_m2がある場合はOK
            continue

        # area_m2が空欄の場合の検証
        # 1. calculationがある場合は必ず警告（どちらのタイプでも）
        if calculation:
            warnings.append(
                f"部屋「{name}」に計算根拠（{calculation}）がありますが、area_m2が空欄です。"
            )
            warning_rooms.append(name)
        # 2. tatamiがある場合は必ず警告（どちらのタイプでも）
        elif not tatami_is_empty:
            warnings.append(
                f"部屋「{name}」に帖数（{tatami}）がありますが、area_m2が空欄です。"
            )
            warning_rooms.append(name)
        # 3. detailed図面の場合のみ、仕上表に記載されている室は計算が必要
        elif diagram_type == "detailed":
            # 仕上表に記載があるかどうかをチェック（床、壁、天井などの情報があるか）
            has_finish_info = any(
                not _is_empty_value(row.get(key))
                for key in [
                    "floor",
                    "wall",
                    "ceiling",
                    "baseboard",
                    "床",
                    "壁",
                    "天井",
                    "巾木",
                ]
            )
            if has_finish_info:
                warnings.append(
                    f"部屋「{name}」は仕上表に記載されていますが、area_m2が空欄です。寸法を読み取って計算してください。"
                )
                warning_rooms.append(name)
        # simple図面の場合は、記載がなければ空欄でもOK

    return warnings, found_rooms, warning_rooms


def _get_function_calls(response):
    """Extract all function calls from all parts of all candidates."""
    func_calls = []
    try:
        candidates = response.candidates or []
    except Exception:
        return []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            func_call = getattr(part, "function_call", None)
            if func_call:
                func_calls.append(func_call)
    return func_calls


def _get_response_text(response):
    """Get full text from the model response. Uses parts when response.text is empty or raises.
    Gemini may return mixed parts (e.g. thought + text or function_call + text); .text can then
    be empty or raise ValueError. This helper concatenates text from all text parts.
    """
    try:
        t = getattr(response, "text", None)
        if t and isinstance(t, str) and t.strip():
            return t
    except (ValueError, AttributeError, TypeError):
        pass
    out = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text and isinstance(text, str):
                out.append(text)
    return "".join(out).strip() if out else ""


def _execute_function_call(func_call):
    name = getattr(func_call, "name", "")
    args = getattr(func_call, "args", None) or {}
    handler = SKILL_REGISTRY.get(name)
    if not handler:
        return name, args, {"error": f"Unknown tool: {name}"}
    try:
        result = handler(**args)
        return name, args, {"result": result}
    except Exception as exc:
        return name, args, {"error": str(exc)}


def _generate_with_tools(client, model_name, parts, generation_config):
    """Handle chat + tool execution loop with the google-genai models API.

    Returns:
        tuple: (response, tool_calls_log) where tool_calls_log is a list of dicts
               containing name, args, and result for each tool call.
    """
    if client is None:
        raise RuntimeError("Vertex AI client is not initialized.")

    config = types.GenerateContentConfig(
        tools=TOOLS,
        **generation_config,
    )

    messages = [types.Content(role="user", parts=parts)]
    response = client.models.generate_content(
        model=model_name,
        contents=messages,
        config=config,
    )

    tool_calls_log = []

    for _ in range(6):
        func_calls = _get_function_calls(response)
        if not func_calls:
            break

        tool_responses = []
        for func_call in func_calls:
            name, args, payload = _execute_function_call(func_call)
            tool_part = types.Part.from_function_response(name=name, response=payload)
            tool_responses.append(tool_part)

            # Log the tool call
            tool_calls_log.append(
                {
                    "name": name,
                    "args": dict(args) if args else {},
                    "result": payload,
                }
            )
            print(f"[Tool Call] {name}({args}) -> {payload}")

        # Append model turn and tool responses to the conversation history
        messages.append(response.candidates[0].content)
        messages.append(types.Content(role="user", parts=tool_responses))

        response = client.models.generate_content(
            model=model_name,
            contents=messages,
            config=config,
        )
    return response, tool_calls_log


def _is_pdf_upload(file: UploadFile) -> bool:
    name = (file.filename or "").lower()
    return name.endswith(".pdf")


CUSTOMER_JUDGMENT_COLUMN_CANDIDATES = [
    "総合判定",
    "照合結果",
    "総合判定(◯/✗)",
    "総合判定(○/×)",
]
DIFF_NOTE_TEXT = "※ 台数差 / 容量差は 電気図 - 機械図"

CUSTOMER_SUMMARY_COLUMNS = [
    ("総合判定", CUSTOMER_JUDGMENT_COLUMN_CANDIDATES),
    ("機器ID照合", ["機器ID照合"]),
    ("判定理由", ["判定理由", "不一致内容", "確認理由", "不一致理由"]),
    ("機器ID", ["機器ID", "機器番号", "機械番号"]),
    ("電気図 台数", ["電気図 台数", "raster_match_count", "raster_台数_calc"]),
    ("電気図 記載名", ["電気図 記載名", "電気図記載名"]),
    (
        "電気図 容量(kW)",
        ["電気図 容量(kW)", "電気図 容量合計(kW)", "raster_容量(kW)_sum"],
    ),
    ("電気図 図面番号", ["電気図 図面番号", "図面番号", "図番"]),
    ("電気図 記載トレース", ["電気図 記載トレース"]),
]
CUSTOMER_SUMMARY_JUDGMENT_COLUMNS = {"総合判定", "機器ID照合"}

CUSTOMER_DISPLAY_SINGLE_COLUMNS = [
    ("機器ID", "機器ID", ["機器ID", "機器番号", "機械番号"]),
    ("ID照合", "ID照合", ["機器ID照合"]),
]
CUSTOMER_DISPLAY_GROUP_COLUMNS = [
    (
        "図面番号",
        [
            ("図面番号_電気図", "電気図", ["電気図 図面番号", "図面番号", "図番"]),
            ("図面番号_機械図", "機械図", ["機械図 図面番号", "機械図図面番号"]),
        ],
    ),
    (
        "容量（KW）",
        [
            (
                "容量_電気図",
                "電気図",
                ["電気図 容量(kW)", "電気図 容量合計(kW)", "raster_容量(kW)_sum"],
            ),
            (
                "容量_機械図",
                "機械図",
                [
                    "機械図 判定採用容量(kW)",
                    "機械図 消費電力(kW)",
                    "機械図 容量合計(kW)",
                    "vector_容量(kW)_calc",
                ],
            ),
            ("容量_差分", "差分", ["容量差(kW)", "容量差分(kW)"]),
        ],
    ),
    (
        "台数",
        [
            (
                "台数_電気図",
                "電気図",
                ["電気図 台数", "raster_match_count", "raster_台数_calc"],
            ),
            ("台数_機械図", "機械図", ["機械図 台数", "台数", "vector_台数_numeric"]),
            ("台数_差分", "差分", ["台数差", "台数差（電気図-機械図）", "台数差分"]),
        ],
    ),
]


def _customer_display_leaf_columns() -> list[tuple[str, str, list[str]]]:
    columns = list(CUSTOMER_DISPLAY_SINGLE_COLUMNS)
    for _group_label, children in CUSTOMER_DISPLAY_GROUP_COLUMNS:
        columns.extend(children)
    return columns


def _normalize_header_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return normalized.replace(" ", "").replace("　", "")


def _pick_first_column_value(row: dict[str, str], candidates: list[str]) -> str:
    for key in candidates:
        value = row.get(key)
        if value is not None:
            return str(value)
    normalized_map = {_normalize_header_token(k): v for k, v in row.items()}
    for key in candidates:
        value = normalized_map.get(_normalize_header_token(key))
        if value is not None:
            return str(value)
    return ""


def _normalize_judgment(value: str) -> str:
    text = str(value or "").strip()
    if text in {"一致", "○", "◯", "match"}:
        return "◯"
    if text in {"不一致", "×", "✗", "mismatch"}:
        return "✗"
    if text in {"判定不可", "要確認", "review"}:
        return "要確認"
    return text


def _normalize_id_match_symbol(value: str) -> str:
    normalized = _normalize_judgment(value)
    if normalized == "◯":
        return "○"
    if normalized == "✗":
        return "×"
    return normalized


def _map_customer_summary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    mapped_rows: list[dict[str, str]] = []
    for row in rows:
        mapped_row: dict[str, str] = {}
        for label, candidates in CUSTOMER_SUMMARY_COLUMNS:
            value = _pick_first_column_value(row, candidates)
            if label in CUSTOMER_SUMMARY_JUDGMENT_COLUMNS:
                value = _normalize_judgment(value)
            mapped_row[label] = value
        mapped_rows.append(mapped_row)
    return mapped_rows


def _map_customer_display_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    mapped_rows: list[dict[str, str]] = []
    for row in rows:
        mapped_row: dict[str, str] = {}
        for key, _label, candidates in _customer_display_leaf_columns():
            value = _pick_first_column_value(row, candidates)
            if key == "ID照合":
                value = _normalize_id_match_symbol(value)
            mapped_row[key] = value
        mapped_rows.append(mapped_row)
    return mapped_rows


def _build_customer_table_header_html() -> str:
    th_class = "border border-stone-300 bg-stone-50 px-3 py-2 text-center text-sm font-semibold"
    th_style = "text-align:center;"
    top_row_cells = []
    for _key, label, _candidates in CUSTOMER_DISPLAY_SINGLE_COLUMNS:
        top_row_cells.append(
            f'<th class="{th_class}" style="{th_style}" rowspan="2">{html.escape(label)}</th>'
        )
    for group_label, children in CUSTOMER_DISPLAY_GROUP_COLUMNS:
        top_row_cells.append(
            f'<th class="{th_class}" style="{th_style}" colspan="{len(children)}">{html.escape(group_label)}</th>'
        )

    bottom_row_cells = []
    for _group_label, children in CUSTOMER_DISPLAY_GROUP_COLUMNS:
        for _key, child_label, _candidates in children:
            bottom_row_cells.append(
                f'<th class="{th_class}" style="{th_style}">{html.escape(child_label)}</th>'
            )

    return f"<tr>{''.join(top_row_cells)}</tr><tr>{''.join(bottom_row_cells)}</tr>"


def _compute_customer_summary(
    mapped_rows: list[dict[str, str]],
    *,
    vector_row_count: Optional[int] = None,
    raster_row_count: Optional[int] = None,
) -> dict[str, int]:
    equipment_count = 0
    panel_count = 0
    id_match_count = 0
    mismatch_count = 0
    review_count = 0

    for row in mapped_rows:
        judgment = _normalize_judgment(row.get("総合判定", ""))
        id_match = _normalize_judgment(row.get("機器ID照合", ""))
        reason = str(row.get("判定理由", "") or "").strip()
        equipment_id = str(row.get("機器ID", "") or "").strip()
        panel_units = parse_float_or_none(row.get("電気図 台数", ""))

        panel_has_detail = any(
            str(row.get(key, "") or "").strip()
            for key in [
                "電気図 記載名",
                "電気図 容量(kW)",
                "電気図 図面番号",
                "電気図 記載トレース",
            ]
        )

        if vector_row_count is None and equipment_id and reason != "機械図に記載なし":
            equipment_count += 1

        if raster_row_count is None and (
            panel_has_detail or (panel_units is not None and panel_units > 0)
        ):
            panel_count += 1

        if id_match == "◯":
            id_match_count += 1

        if judgment == "✗":
            mismatch_count += 1
        elif judgment == "要確認":
            review_count += 1

    if vector_row_count is not None:
        equipment_count = max(0, int(vector_row_count))
    if raster_row_count is not None:
        panel_count = max(0, int(raster_row_count))

    return {
        "機械図記載": equipment_count,
        "電気図記載": panel_count,
        "ID照合一致": id_match_count,
        "不一致": mismatch_count,
        "要確認": review_count,
    }


def _build_customer_summary_html(
    mapped_rows: list[dict[str, str]],
    *,
    vector_row_count: Optional[int] = None,
    raster_row_count: Optional[int] = None,
) -> str:
    summary = _compute_customer_summary(
        mapped_rows,
        vector_row_count=vector_row_count,
        raster_row_count=raster_row_count,
    )
    parts = [
        f"{label}：{summary[label]}件"
        for label in ["機械図記載", "電気図記載", "ID照合一致", "不一致", "要確認"]
    ]
    summary_cells = "".join(
        f'<div class="rounded border border-emerald-200 bg-white px-3 py-2 text-sm text-emerald-900">'
        f"{html.escape(part)}</div>"
        for part in parts
    )
    return f'<div class="customer-summary-grid mb-3 grid grid-cols-2 gap-2 md:grid-cols-5">{summary_cells}</div>'


def _build_customer_table_html(
    unified_csv_path: Path,
    *,
    vector_row_count: Optional[int] = None,
    raster_row_count: Optional[int] = None,
) -> str:
    rows = read_csv_dict_rows(unified_csv_path)
    diff_note_html = (
        f'<p class="mt-2 text-xs text-stone-600">{html.escape(DIFF_NOTE_TEXT)}</p>'
    )
    header_html = _build_customer_table_header_html()
    display_columns = _customer_display_leaf_columns()
    display_column_count = len(display_columns)
    summary_rows = _map_customer_summary_rows(rows)
    display_rows = _map_customer_display_rows(rows)

    if not rows:
        summary_html = _build_customer_summary_html(
            summary_rows,
            vector_row_count=vector_row_count,
            raster_row_count=raster_row_count,
        )
        return (
            summary_html
            + '<table class="customer-compare-table w-full border-collapse border border-stone-300 text-sm">'
            f"<thead>{header_html}</thead>"
            '<tbody><tr><td class="border border-stone-300 px-3 py-6 text-center text-stone-500"'
            f' colspan="{display_column_count}">データがありません</td></tr></tbody></table>'
            f"{diff_note_html}"
        )

    summary_html = _build_customer_summary_html(
        summary_rows,
        vector_row_count=vector_row_count,
        raster_row_count=raster_row_count,
    )

    body_rows = []
    for mapped_row in display_rows:
        mapped_cells = []
        for key, _label, _candidates in display_columns:
            value = mapped_row[key]
            align_class = "text-left" if key == "機器ID" else "text-center"
            mapped_cells.append(
                f'<td class="border border-stone-300 px-3 py-2 text-sm {align_class}">{html.escape(value)}</td>'
            )
        body_rows.append("<tr>" + "".join(mapped_cells) + "</tr>")

    return (
        summary_html
        + '<table class="customer-compare-table w-full border-collapse border border-stone-300 text-sm">'
        f"<thead>{header_html}</thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        f"{diff_note_html}"
    )


def _single_line_message(message: object) -> str:
    """Backward-compat alias."""
    return _single_line_message_renderer(message)


def _exception_message(exc: Exception) -> str:
    """Backward-compat alias."""
    return _exception_message_util(exc)


def _is_parallel_extract_enabled() -> bool:
    raw = os.getenv("ME_CHECK_PARALLEL_EXTRACT", "1").strip().lower()
    return raw not in {"0", "false"}


def _render_customer_success_html(unified_job_id: str, table_html: str) -> str:
    safe_job_id = html.escape(unified_job_id, quote=True)
    download_url = f"/jobs/{unified_job_id}/unified.csv"
    safe_download_url = html.escape(download_url, quote=True)
    return f"""
    <section class="relative rounded-lg border border-emerald-300 bg-emerald-50 p-4 shadow-sm"
      data-status="success"
      data-unified-job-id="{safe_job_id}"
      data-download-url="{safe_download_url}">
      <button
        type="button"
        title="表を拡大表示"
        aria-label="表を拡大表示"
        data-action="expand-customer-table"
        class="group absolute right-12 top-3 inline-flex h-8 w-8 items-center justify-center rounded border border-emerald-700 bg-white text-emerald-700 hover:bg-emerald-100"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4" aria-hidden="true">
          <path d="M8 3H3v5"></path>
          <path d="M16 3h5v5"></path>
          <path d="M8 21H3v-5"></path>
          <path d="M16 21h5v-5"></path>
        </svg>
        <span class="pointer-events-none absolute right-10 top-1/2 hidden -translate-y-1/2 whitespace-nowrap rounded bg-stone-900 px-2 py-1 text-xs text-white group-hover:block">
          表を拡大表示
        </span>
      </button>
      <a
        href="{safe_download_url}"
        title="CSVをダウンロード"
        aria-label="CSVをダウンロード"
        class="group absolute right-3 top-3 inline-flex h-8 w-8 items-center justify-center rounded border border-emerald-700 bg-white text-emerald-700 hover:bg-emerald-100"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4" aria-hidden="true">
          <path d="M12 3v11"></path>
          <path d="m8 10 4 4 4-4"></path>
          <path d="M4 20h16"></path>
        </svg>
        <span class="pointer-events-none absolute right-10 top-1/2 hidden -translate-y-1/2 whitespace-nowrap rounded bg-stone-900 px-2 py-1 text-xs text-white group-hover:block">
          CSVをダウンロード
        </span>
      </a>
      <div class="text-sm font-semibold text-emerald-800">処理が完了しました</div>
      <div class="mt-4 overflow-x-auto" data-role="customer-table-container">{table_html}</div>
    </section>
    """


def _render_customer_error_html(stage: str, message: str) -> str:
    safe_stage = html.escape(stage, quote=True)
    safe_message = html.escape(_single_line_message(message), quote=True)
    return f"""
    <section class="rounded-lg border border-red-300 bg-red-50 p-4 shadow-sm"
      data-status="error"
      data-stage="{safe_stage}">
      <div class="text-sm font-semibold text-red-800">処理に失敗しました</div>
      <div class="mt-1 text-sm">stage: <code class="font-mono">{safe_stage}</code></div>
      <div class="mt-1 text-sm">message: {safe_message}</div>
    </section>
    """


def _run_e055_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="e055", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "e055.csv"
    debug_dir = Path("/tmp") / "plan2table" / "debug" / job.job_id
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


def _run_e251_job(file_bytes: bytes, source_filename: str):
    if not vision_service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not configured.")

    job = create_job(kind="e251", source_filename=source_filename)
    input_pdf_path = job.job_dir / "input.pdf"
    input_pdf_path.write_bytes(file_bytes)
    csv_path = job.job_dir / "e251.csv"
    debug_dir = Path("/tmp") / "plan2table" / "debug" / job.job_id
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


def _run_e142_job(file_bytes: bytes, source_filename: str):
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


def _run_raster_job(file_bytes: bytes, source_filename: str):
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


def _run_vector_job(file_bytes: bytes, source_filename: str):
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


def _resolve_existing_csv_or_404(job_id: str, kind: str) -> Path:
    try:
        csv_path = resolve_job_csv_path(job_id=job_id, kind=kind)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.parent.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="CSV not found")
    return csv_path


def _run_unified_job(raster_job_id: str, vector_job_id: str):
    raster_csv_path = _resolve_existing_csv_or_404(job_id=raster_job_id, kind="raster")
    vector_csv_path = _resolve_existing_csv_or_404(job_id=vector_job_id, kind="vector")

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


@app.post("/area/upload", response_class=HTMLResponse)
async def handle_area_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """

    try:
        # Read file content
        file_bytes = await file.read()

        # Extract text from PDF for rule-based fallback
        extracted_text = extract_text_from_pdf(file_bytes)
        regex_summary = extract_summary_areas(extracted_text)

        # Prepare the request for Vertex AI
        pdf_part = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")

        prompt_text = load_prompt("area_extract")

        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 16384,
        }

        def _run_generation(extra_instruction=""):
            combined_prompt = prompt_text
            if extra_instruction:
                combined_prompt = f"{prompt_text}\n\n{extra_instruction}"
            combined_prompt_part = types.Part.from_text(text=combined_prompt)
            response, tool_calls_log = _generate_with_tools(
                genai_client,
                MODEL_NAME,
                [pdf_part, combined_prompt_part],
                generation_config=generation_config,
            )
            raw_text = _get_response_text(response) or ""
            data = _extract_json(raw_text)
            return response, tool_calls_log, raw_text, data

        _response, tool_calls_log, raw_text, data = _run_generation()
        if not isinstance(data, dict):
            return render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")

        # 図面タイプを取得
        diagram_type = _get_diagram_type(data)
        print(f"[Debug] 図面タイプ: {diagram_type}")

        # 柔軟な検証: 読み取れる情報があるのに空欄になっている場合のみ警告
        warnings, found_rooms, warning_rooms = _validate_room_areas(data)

        if warnings:
            # Debug: log warnings
            print(f"[Debug] 検出された室: {found_rooms}")
            print(f"[Debug] 警告: {warnings}")

            # 警告がある場合のみ、再生成を試みる（detailed図面のみ）
            if diagram_type == "detailed":
                extra_instruction = (
                    "【追加指示 - 必ず実行してください】\n"
                    "この図面は寸法線がある「detailed」タイプです。\n"
                    "以下の部屋について、area_m2が空欄（「-」含む）になっています。\n"
                    "これらの部屋は仕上表に記載されているため、図面から寸法線を読み取って面積を計算する必要があります。\n\n"
                    f"**面積が未計算の部屋**: {', '.join(warning_rooms)}\n\n"
                    "【対応手順】\n"
                    "1. 各部屋の寸法線を図面から読み取る（幅mm × 奥行mm）\n"
                    "2. calculate_room_area_from_dimensions ツールを使用して面積を計算\n"
                    "3. room_areasのarea_m2フィールドに計算結果を記載\n"
                    "4. calculationフィールドに計算根拠（例: 「幅1000mm × 奥行1500mm = 1.50㎡」）を記載\n\n"
                    "推測や一般値は禁止。寸法(mm)を明示してください。\n"
                    "「-」や空欄にせず、必ず数値で埋めてください。"
                )
                _response, tool_calls_log, raw_text, data = _run_generation(
                    extra_instruction
                )
                if not isinstance(data, dict):
                    return render_parse_error(
                        raw_text, "JSONオブジェクトが見つかりません。"
                    )

                # 再検証
                warnings, found_rooms, warning_rooms = _validate_room_areas(data)

            if warnings:
                # 警告があってもエラーにはしない（柔軟性のため）
                print(f"[Warning] 以下の警告がありますが、処理を続行します: {warnings}")

        report_md = data.get("report_markdown", "")
        if not report_md:
            return render_parse_error(
                raw_text, "レポート内容（report_markdown）が空です。"
            )

        # Convert Markdown to HTML and sanitize (markdown does not strip raw HTML)
        report_html = markdown.markdown(report_md, extensions=["tables", "fenced_code"])
        report_html = bleach.clean(
            report_html,
            tags={
                "p",
                "ul",
                "ol",
                "li",
                "strong",
                "em",
                "code",
                "pre",
                "table",
                "thead",
                "tbody",
                "tr",
                "th",
                "td",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "a",
            },
            attributes={"a": ["href"]},
            protocols={"http", "https", "mailto"},
            strip=True,
        )

        # Build debug info (outputs to browser console)
        debug_script = build_debug_script(
            extracted_text, regex_summary, raw_text, tool_calls_log
        )

        # Wrapping in a styled div for better look
        styled_report = f"""
        <div class="prose max-w-5xl mx-auto bg-paper p-6 rounded-sm border border-stone/30 space-y-6">
            {report_html}
        </div>
        """

        return styled_report + debug_script

    except Exception as e:
        # Log the error for debugging (on the server console)
        print(f"Error processing upload: {e}")
        safe_error = html.escape(str(e), quote=True)
        return f"""
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error Processing Request:</strong><br>
            {safe_error}
        </div>
        """


@app.post("/upload", response_class=HTMLResponse)
async def handle_upload_compat(file: UploadFile = File(...)):
    # Backward compatibility route.
    return await handle_area_upload(file)


@app.post("/e-055/upload", response_class=HTMLResponse)
async def handle_e055_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
        return render_extractor_error_html("e055", "Please upload a valid PDF file.")
    if not vision_service_account_json:
        return render_extractor_error_html("e055", "VISION_SERVICE_ACCOUNT_KEY is not configured.")

    try:
        file_bytes = await file.read()
        job, profile = _run_e055_job(
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        table_html = build_equipment_table_html(job.job_dir / "e055.csv", "e055")
        return render_extractor_success_html(
            "e055", job.job_id, table_html, int(profile["rows"])
        )
    except Exception as exc:
        print(f"E-055 extraction failed: {exc}")
        return render_extractor_error_html("e055", str(exc))


@app.post("/e-251/upload", response_class=HTMLResponse)
async def handle_e251_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
        return render_extractor_error_html("e251", "Please upload a valid PDF file.")
    if not vision_service_account_json:
        return render_extractor_error_html("e251", "VISION_SERVICE_ACCOUNT_KEY is not configured.")

    try:
        file_bytes = await file.read()
        job, profile = _run_e251_job(
            file_bytes=file_bytes,
            source_filename=file.filename or "upload.pdf",
        )
        table_html = build_equipment_table_html(job.job_dir / "e251.csv", "e251")
        return render_extractor_success_html(
            "e251", job.job_id, table_html, int(profile["rows"])
        )
    except Exception as exc:
        print(f"E-251 extraction failed: {exc}")
        return render_extractor_error_html("e251", str(exc))


@app.post("/e-142/upload", response_class=HTMLResponse)
async def handle_e142_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
        return render_extractor_error_html("e142", "Please upload a valid PDF file.")
    if not vision_service_account_json:
        return render_extractor_error_html("e142", "VISION_SERVICE_ACCOUNT_KEY is not configured.")

    try:
        file_bytes = await file.read()
        job, profile = await asyncio.to_thread(
            _run_e142_job,
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


@app.post("/customer/run", response_class=HTMLResponse)
async def handle_customer_run(
    panel_file: UploadFile = File(...),
    equipment_file: UploadFile = File(...),
):
    if not _is_pdf_upload(panel_file):
        return _render_customer_error_html(
            stage="panel->raster",
            message="Please upload a valid PDF file for panel_file.",
        )
    if not _is_pdf_upload(equipment_file):
        return _render_customer_error_html(
            stage="equipment->vector",
            message="Please upload a valid PDF file for equipment_file.",
        )

    panel_file_bytes = await panel_file.read()
    equipment_file_bytes = await equipment_file.read()
    parallel_extract_enabled = _is_parallel_extract_enabled()
    raster_profile: Optional[dict] = None
    vector_profile: Optional[dict] = None

    if parallel_extract_enabled:
        raster_result, vector_result = await asyncio.gather(
            asyncio.to_thread(
                _run_raster_job,
                file_bytes=panel_file_bytes,
                source_filename=panel_file.filename or "panel.pdf",
            ),
            asyncio.to_thread(
                _run_vector_job,
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
            return _render_customer_error_html(
                stage="panel->raster",
                message=_exception_message(raster_exc),
            )
        if raster_exc:
            print(f"Customer flow failed at panel->raster: {raster_exc}")
            return _render_customer_error_html(
                stage="panel->raster",
                message=_exception_message(raster_exc),
            )
        if vector_exc:
            print(f"Customer flow failed at equipment->vector: {vector_exc}")
            return _render_customer_error_html(
                stage="equipment->vector",
                message=_exception_message(vector_exc),
            )
        raster_job, raster_profile = raster_result
        vector_job, vector_profile = vector_result
    else:
        try:
            raster_job, raster_profile = await asyncio.to_thread(
                _run_raster_job,
                file_bytes=panel_file_bytes,
                source_filename=panel_file.filename or "panel.pdf",
            )
        except Exception as exc:
            print(f"Customer flow failed at panel->raster: {exc}")
            return _render_customer_error_html(
                stage="panel->raster",
                message=_exception_message(exc),
            )

        try:
            vector_job, vector_profile = await asyncio.to_thread(
                _run_vector_job,
                file_bytes=equipment_file_bytes,
                source_filename=equipment_file.filename or "equipment.pdf",
            )
        except Exception as exc:
            print(f"Customer flow failed at equipment->vector: {exc}")
            return _render_customer_error_html(
                stage="equipment->vector",
                message=_exception_message(exc),
            )

    try:
        unified_job, _ = await asyncio.to_thread(
            _run_unified_job,
            raster_job_id=raster_job.job_id,
            vector_job_id=vector_job.job_id,
        )
        unified_csv_path = unified_job.job_dir / "unified.csv"
        table_html = _build_customer_table_html(
            unified_csv_path,
            vector_row_count=int((vector_profile or {}).get("rows", 0)),
            raster_row_count=int((raster_profile or {}).get("rows", 0)),
        )
        return _render_customer_success_html(
            unified_job_id=unified_job.job_id,
            table_html=table_html,
        )
    except Exception as exc:
        print(f"Customer flow failed at unified: {exc}")
        return _render_customer_error_html(
            stage="unified",
            message=_exception_message(exc),
        )


@app.post("/raster/upload", response_class=HTMLResponse)
async def handle_raster_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
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
        job, profile = _run_raster_job(
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


@app.post("/vector/upload", response_class=HTMLResponse)
async def handle_vector_upload(file: UploadFile = File(...)):
    if not _is_pdf_upload(file):
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """

    try:
        file_bytes = await file.read()
        job, profile = _run_vector_job(
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


@app.post("/unified/merge", response_class=HTMLResponse)
async def handle_unified_merge(
    raster_job_id: UUID = Form(...),
    vector_job_id: UUID = Form(...),
):
    raster_job_id_str = str(raster_job_id)
    vector_job_id_str = str(vector_job_id)

    try:
        job, profile = _run_unified_job(
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
