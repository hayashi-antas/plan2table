import os
import csv
import json
import html
import unicodedata
from pathlib import Path
from uuid import UUID
from google import genai
from google.genai import types
from fastapi import FastAPI, File, UploadFile, Request, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn
import markdown
from prompts import load_prompt
from extractors.text_extractor import extract_text_from_pdf
from extractors.area_regex import extract_summary_areas
from extractors.tool_definitions import TOOLS, SKILL_REGISTRY
from extractors.raster_extractor import extract_raster_pdf
from extractors.vector_extractor import extract_vector_pdf_four_columns
from extractors.job_store import create_job, resolve_job_csv_path, save_metadata
from extractors.unified_csv import merge_vector_raster_csv

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Initialize Vertex AI (new google-genai client)
# We use the environment variable GOOGLE_CLOUD_PROJECT as requested.
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("VERTEX_LOCATION", "global")
MODEL_NAME = os.getenv("VERTEX_MODEL_NAME", "gemini-3-pro-preview")
genai_client = None
vision_service_account_json = os.getenv("VISION_SERVICE_ACCOUNT_KEY", "")
# 固定の必須部屋リストは削除 - 図面から読み取れる情報を柔軟に処理

# Handle Credentials for Hugging Face Spaces
# If VERTEX_SERVICE_ACCOUNT_KEY env var exists (JSON content), write it to a file.
# Backward compatibility: fallback to GCP_SERVICE_ACCOUNT_KEY.
vertex_service_account_json = os.getenv("VERTEX_SERVICE_ACCOUNT_KEY") or os.getenv("GCP_SERVICE_ACCOUNT_KEY")
if vertex_service_account_json:
    cred_file_path = "gcp_credentials.json"
    with open(cred_file_path, "w") as f:
        f.write(vertex_service_account_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file_path
    print(f"Credentials saved to {cred_file_path}")

try:
    if project_id:
        genai_client = genai.Client(vertexai=True, project=project_id, location=location)
    else:
        genai_client = genai.Client(vertexai=True, location=location)
except Exception as exc:
    print(f"Failed to initialize Vertex AI client: {exc}")

def _stringify_cell(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

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
        candidate = cleaned[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

def _normalize_columns(columns, rows):
    normalized = []
    seen = set()
    for col in columns:
        if not isinstance(col, dict):
            continue
        key = col.get("key") or ""
        label = col.get("label") or key
        if not key:
            key = f"col_{len(normalized) + 1}"
        key = str(key)
        if key in seen:
            key = f"{key}_{len(normalized) + 1}"
        normalized.append({
            "key": key,
            "label": str(label) if label is not None else key,
            "hint": str(col.get("hint") or "")
        })
        seen.add(key)
    if not normalized:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in row.keys():
                key = str(key)
                if key in seen:
                    continue
                normalized.append({"key": key, "label": key, "hint": ""})
                seen.add(key)
    return normalized

def _build_table_html(columns, rows):
    safe_columns = _normalize_columns(columns, rows)
    safe_rows = [row for row in rows if isinstance(row, dict)]
    if not safe_columns:
        safe_columns = [{"key": "_empty", "label": "データなし", "hint": ""}]

    table_class = "min-w-full table-auto border border-stone/30 overflow-hidden bg-paper text-ink"
    thead_class = "bg-paper-dark"
    th_class = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-wood-dark border-b border-wood"
    td_class = "px-4 py-3 text-sm text-ink-light border-b border-stone/20 whitespace-pre-wrap"
    row_class = "hover:bg-paper-dark/50"
    empty_td_class = "px-4 py-6 text-sm text-ink-muted text-center"

    header_cells = []
    for col in safe_columns:
        label = html.escape(col["label"], quote=True)
        header_cells.append(f"<th class=\"{th_class}\">{label}</th>")

    body_rows = []
    if not safe_rows:
        colspan = len(safe_columns)
        body_rows.append(
            f"<tr class=\"{row_class}\"><td colspan=\"{colspan}\" class=\"{empty_td_class}\">データがありません</td></tr>"
        )
    else:
        for row in safe_rows:
            cells = []
            for col in safe_columns:
                key = col["key"]
                value = _stringify_cell(row.get(key, ""))
                cell_text = html.escape(value, quote=True)
                cells.append(f"<td class=\"{td_class}\">{cell_text}</td>")
            body_rows.append(f"<tr class=\"{row_class}\">" + "".join(cells) + "</tr>")

    return (
        f"<table class=\"{table_class}\">"
        f"<thead class=\"{thead_class}\"><tr>{''.join(header_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
    )

def _build_summary_html(summary):
    if not isinstance(summary, dict) or not summary:
        return ""
    labels = {
        "exclusive_area_m2": "住戸専用面積(m2)",
        "balcony_area_m2": "バルコニー面積(m2)",
        "total_area_m2": "延床面積(m2)",
        "unit_type": "間取りタイプ",
        "floor": "階数",
        "orientation": "方位",
    }
    rows = []
    for key, label in labels.items():
        value = _stringify_cell(summary.get(key, ""))
        if value == "":
            continue
        rows.append((label, value))
    if not rows:
        return ""

    card_class = "mb-6 rounded-sm border border-stone/30 bg-paper-dark p-4 text-ink"
    title_class = "mb-3 text-sm font-semibold uppercase tracking-wider text-wood-dark"
    grid_class = "grid grid-cols-1 gap-3 md:grid-cols-2"
    label_class = "text-xs uppercase tracking-wider text-ink-muted"
    value_class = "text-sm text-ink-light"
    items = []
    for label, value in rows:
        items.append(
            f"<div><div class=\"{label_class}\">{html.escape(label)}</div>"
            f"<div class=\"{value_class}\">{html.escape(str(value))}</div></div>"
        )
    return (
        f"<section class=\"{card_class}\">"
        f"<div class=\"{title_class}\">住戸概要</div>"
        f"<div class=\"{grid_class}\">{''.join(items)}</div>"
        f"</section>"
    )

def _render_parse_error(raw_text, reason):
    snippet = (raw_text or "").strip() or "(empty response)"
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "\n... (truncated)"
    snippet = html.escape(snippet, quote=True)
    reason = html.escape(reason, quote=True)
    return f"""
    <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
        <strong>JSON解析に失敗しました:</strong> {reason}
        <pre class="mt-3 max-h-72 overflow-auto rounded-sm bg-paper-dark p-3 text-xs text-ink-light">{snippet}</pre>
    </div>
    """

def _build_debug_script(extracted_text, regex_summary, raw_text, tool_calls_log=None):
    """Build a script tag that logs debug info to browser console."""
    text_snippet = (extracted_text or "").strip() or "(no text extracted)"
    if len(text_snippet) > 5000:
        text_snippet = text_snippet[:5000] + "\n... (truncated)"

    regex_json_str = json.dumps(regex_summary, ensure_ascii=False, indent=2)
    raw_snippet = (raw_text or "").strip() or "(empty response)"
    if len(raw_snippet) > 5000:
        raw_snippet = raw_snippet[:5000] + "\n... (truncated)"

    # Escape for JavaScript string (handle quotes, newlines, backslashes)
    def js_escape(s):
        return (s
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("${", "\\${"))

    text_escaped = js_escape(text_snippet)
    regex_escaped = js_escape(regex_json_str)
    raw_escaped = js_escape(raw_snippet)
    
    # Build tool calls log script
    tool_calls_script = ""
    if tool_calls_log:
        tool_calls_json = json.dumps(tool_calls_log, ensure_ascii=False, indent=2)
        tool_calls_escaped = js_escape(tool_calls_json)
        tool_calls_script = f"""
    console.group('%c🔧 Function Calling (ツール呼び出し)', 'font-weight: bold; font-size: 14px; color: #2d5a8a;');
    console.log('%c呼び出されたツール数:', 'font-weight: bold; color: #1e3a5f;', {len(tool_calls_log)});
    const toolCalls = JSON.parse(`{tool_calls_escaped}`);
    toolCalls.forEach((call, index) => {{
        console.group('%c[' + (index + 1) + '] ' + call.name, 'font-weight: bold; color: #b87333;');
        console.log('%c引数:', 'color: #5c5243;', call.args);
        console.log('%c結果:', 'color: #5c5243;', call.result);
        console.groupEnd();
    }});
    console.groupEnd();
"""
    else:
        tool_calls_script = """
    console.log('%c🔧 Function Calling: ツールは呼び出されませんでした', 'font-weight: bold; font-size: 14px; color: #8a8072;');
"""

    return f"""
    <script>
    console.group('%c📐 図面解析デバッグ情報', 'font-weight: bold; font-size: 14px; color: #8b5a2b;');
    console.log('%c抽出テキスト:', 'font-weight: bold; color: #6b4423;');
    console.log(`{text_escaped}`);
    console.log('%c正規表現ヒット:', 'font-weight: bold; color: #6b4423;');
    console.log(`{regex_escaped}`);
    console.log('%cLLM生レスポンス:', 'font-weight: bold; color: #6b4423;');
    console.log(`{raw_escaped}`);
    console.groupEnd();
    {tool_calls_script}
    </script>
    """


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
                for key in ["floor", "wall", "ceiling", "baseboard", "床", "壁", "天井", "巾木"]
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
            tool_calls_log.append({
                "name": name,
                "args": dict(args) if args else {},
                "result": payload,
            })
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

def _csv_profile(csv_path: Path) -> dict:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return {"rows": 0, "columns": []}
    return {"rows": max(0, len(rows) - 1), "columns": rows[0]}


def _render_job_result_html(kind: str, job_id: str, rows: int, columns: list[str]) -> str:
    label_map = {
        "raster": "Raster",
        "vector": "Vector",
        "unified": "Unified",
    }
    label = label_map.get(kind, kind.capitalize())
    download_path = f"/jobs/{job_id}/{kind}.csv"
    columns_html = ", ".join(html.escape(c) for c in columns) if columns else "-"
    return f"""
    <section class="mt-4 rounded-sm border border-stone/30 bg-paper-dark p-4 text-ink" data-kind="{html.escape(kind)}" data-job-id="{html.escape(job_id)}">
        <div class="text-sm font-semibold text-wood-dark">{label} CSV作成完了</div>
        <div class="mt-2 text-sm">Job ID: <code class="font-mono">{html.escape(job_id)}</code></div>
        <div class="mt-1 text-sm">Rows: {rows}</div>
        <div class="mt-1 text-sm">Columns: {columns_html}</div>
        <a href="{download_path}" class="mt-3 inline-block rounded-sm border border-wood px-3 py-2 text-sm font-semibold text-wood hover:bg-paper">
            CSVをダウンロード
        </a>
    </section>
    """


def _is_pdf_upload(file: UploadFile) -> bool:
    name = (file.filename or "").lower()
    return name.endswith(".pdf")


CUSTOMER_JUDGMENT_COLUMN_CANDIDATES = [
    "総合判定",
    "総合判定(◯/✗)",
    "総合判定(○/×)",
]

CUSTOMER_TABLE_COLUMNS = [
    ("判定(◯/✗)", CUSTOMER_JUDGMENT_COLUMN_CANDIDATES),
    ("機器番号", ["機器番号"]),
    ("機器名", ["名称"]),
    ("機器kW", ["vector_消費電力(kW)_per_unit"]),
    ("機器台数", ["vector_台数_numeric"]),
    ("盤台数", ["raster_台数_calc"]),
    ("合計差(kW)", ["容量差分(kW)"]),
    ("理由", ["不一致理由"]),
]


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


def _normalize_mark(value: str) -> str:
    text = str(value or "").strip()
    return text.replace("○", "◯").replace("×", "✗")


def _read_csv_dict_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _build_customer_table_html(unified_csv_path: Path) -> str:
    rows = _read_csv_dict_rows(unified_csv_path)
    header_cells = "".join(
        f"<th class=\"border border-stone-300 bg-stone-50 px-3 py-2 text-left text-sm font-semibold\">{html.escape(label)}</th>"
        for label, _ in CUSTOMER_TABLE_COLUMNS
    )

    if not rows:
        return (
            "<table class=\"w-full border-collapse border border-stone-300 text-sm\">"
            f"<thead><tr>{header_cells}</tr></thead>"
            "<tbody><tr><td class=\"border border-stone-300 px-3 py-6 text-center text-stone-500\""
            f" colspan=\"{len(CUSTOMER_TABLE_COLUMNS)}\">データがありません</td></tr></tbody></table>"
        )

    body_rows = []
    for row in rows:
        mapped_cells = []
        for label, candidates in CUSTOMER_TABLE_COLUMNS:
            value = _pick_first_column_value(row, candidates)
            if label == "判定(◯/✗)":
                value = _normalize_mark(value)
            mapped_cells.append(
                f"<td class=\"border border-stone-300 px-3 py-2 text-sm\">{html.escape(value)}</td>"
            )
        body_rows.append("<tr>" + "".join(mapped_cells) + "</tr>")

    return (
        "<table class=\"w-full border-collapse border border-stone-300 text-sm\">"
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def _single_line_message(message: object) -> str:
    return " ".join(str(message or "").split())


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return _single_line_message(exc.detail)
    text = _single_line_message(str(exc))
    if text:
        return text
    return exc.__class__.__name__


def _render_customer_success_html(unified_job_id: str, table_html: str) -> str:
    safe_job_id = html.escape(unified_job_id, quote=True)
    download_url = f"/jobs/{unified_job_id}/unified.csv"
    safe_download_url = html.escape(download_url, quote=True)
    return f"""
    <section class="relative rounded-lg border border-emerald-300 bg-emerald-50 p-4 shadow-sm"
      data-status="success"
      data-unified-job-id="{safe_job_id}"
      data-download-url="{safe_download_url}">
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
      <div class="mt-4 overflow-x-auto">{table_html}</div>
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
        page=1,
        dpi=300,
        y_cluster=20.0,
    )
    profile = _csv_profile(csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["raster.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "raster-v1",
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
    profile = _csv_profile(csv_path)
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
    profile = _csv_profile(out_csv_path)
    save_metadata(
        job,
        {
            "csv_files": ["unified.csv"],
            "row_count": profile["rows"],
            "columns": profile["columns"],
            "extractor_version": "unified-v1",
            "source_job_ids": {
                "raster_job_id": raster_job_id,
                "vector_job_id": vector_job_id,
            },
            "extract_result": merge_result,
        },
    )
    return job, profile


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/develop", response_class=HTMLResponse)
async def read_develop(request: Request):
    return templates.TemplateResponse("develop.html", {"request": request})


@app.get("/area", response_class=HTMLResponse)
async def read_area(request: Request):
    return templates.TemplateResponse("area.html", {"request": request})


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
            raw_text = response.text or ""
            data = _extract_json(raw_text)
            return response, tool_calls_log, raw_text, data

        response, tool_calls_log, raw_text, data = _run_generation()
        if not isinstance(data, dict):
            return _render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")

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
                response, tool_calls_log, raw_text, data = _run_generation(extra_instruction)
                if not isinstance(data, dict):
                    return _render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")
                
                # 再検証
                warnings, found_rooms, warning_rooms = _validate_room_areas(data)
            
            if warnings:
                # 警告があってもエラーにはしない（柔軟性のため）
                print(f"[Warning] 以下の警告がありますが、処理を続行します: {warnings}")

        report_md = data.get("report_markdown", "")
        if not report_md:
            return _render_parse_error(raw_text, "レポート内容（report_markdown）が空です。")

        # Convert Markdown to HTML
        report_html = markdown.markdown(
            report_md,
            extensions=['tables', 'fenced_code']
        )

        # Build debug info (outputs to browser console)
        debug_script = _build_debug_script(extracted_text, regex_summary, raw_text, tool_calls_log)
        
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

    try:
        panel_file_bytes = await panel_file.read()
        raster_job, _ = _run_raster_job(
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
        equipment_file_bytes = await equipment_file.read()
        vector_job, _ = _run_vector_job(
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
        unified_job, _ = _run_unified_job(
            raster_job_id=raster_job.job_id,
            vector_job_id=vector_job.job_id,
        )
        unified_csv_path = unified_job.job_dir / "unified.csv"
        table_html = _build_customer_table_html(unified_csv_path)
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
        return _render_job_result_html(
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
        return _render_job_result_html(
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
        return _render_job_result_html(
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


def _download_job_csv(job_id: UUID, kind: str):
    job_id_str = str(job_id)
    try:
        csv_path = resolve_job_csv_path(job_id=job_id_str, kind=kind)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.parent.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="CSV not found")
    return FileResponse(
        path=csv_path,
        media_type="text/csv; charset=utf-8",
        filename=f"{kind}.csv",
    )


@app.get("/jobs/{job_id}/raster.csv")
async def download_raster_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="raster")


@app.get("/jobs/{job_id}/vector.csv")
async def download_vector_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="vector")


@app.get("/jobs/{job_id}/unified.csv")
async def download_unified_csv(job_id: UUID):
    return _download_job_csv(job_id=job_id, kind="unified")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
