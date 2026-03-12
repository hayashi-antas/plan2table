"""HTML rendering helpers for area, job results, and extractor (e055/e251/e142) pages."""

from __future__ import annotations

import csv
import html
import io
import json
import re
from pathlib import Path

from app.core.utils import single_line_message
from extractors.csv_utils import read_csv_dict_rows, read_csv_rows


def stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def normalize_columns(columns: list, rows: list) -> list[dict]:
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
        normalized.append(
            {
                "key": key,
                "label": str(label) if label is not None else key,
                "hint": str(col.get("hint") or ""),
            }
        )
        seen.add(key)
    if not normalized:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for k in row.keys():
                key_str = str(k)
                if key_str in seen:
                    continue
                normalized.append({"key": key_str, "label": key_str, "hint": ""})
                seen.add(key_str)
    return normalized


def build_table_html(columns: list, rows: list) -> str:
    safe_columns = normalize_columns(columns, rows)
    safe_rows = [row for row in rows if isinstance(row, dict)]
    if not safe_columns:
        safe_columns = [{"key": "_empty", "label": "データなし", "hint": ""}]

    table_class = (
        "min-w-full table-auto border border-stone/30 overflow-hidden bg-paper text-ink"
    )
    thead_class = "bg-paper-dark"
    th_class = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-wood-dark border-b border-wood"
    td_class = (
        "px-4 py-3 text-sm text-ink-light border-b border-stone/20 whitespace-pre-wrap"
    )
    row_class = "hover:bg-paper-dark/50"
    empty_td_class = "px-4 py-6 text-sm text-ink-muted text-center"

    header_cells = []
    for col in safe_columns:
        label = html.escape(col["label"], quote=True)
        header_cells.append(f'<th class="{th_class}">{label}</th>')

    body_rows = []
    if not safe_rows:
        colspan = len(safe_columns)
        body_rows.append(
            f'<tr class="{row_class}"><td colspan="{colspan}" class="{empty_td_class}">データがありません</td></tr>'
        )
    else:
        for row in safe_rows:
            cells = []
            for col in safe_columns:
                key = col["key"]
                value = stringify_cell(row.get(key, ""))
                cell_text = html.escape(value, quote=True)
                cells.append(f'<td class="{td_class}">{cell_text}</td>')
            body_rows.append(f'<tr class="{row_class}">' + "".join(cells) + "</tr>")

    return (
        f'<table class="{table_class}">'
        f"<thead class=\"{thead_class}\"><tr>{''.join(header_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
    )


def build_summary_html(summary: dict) -> str:
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
        value = stringify_cell(summary.get(key, ""))
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
            f'<div><div class="{label_class}">{html.escape(label)}</div>'
            f'<div class="{value_class}">{html.escape(str(value))}</div></div>'
        )
    return (
        f'<section class="{card_class}">'
        f'<div class="{title_class}">住戸概要</div>'
        f"<div class=\"{grid_class}\">{''.join(items)}</div>"
        f"</section>"
    )


def render_parse_error(raw_text: str, reason: str) -> str:
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


def build_debug_script(
    extracted_text: str,
    regex_summary: object,
    raw_text: str,
    tool_calls_log: list | None = None,
) -> str:
    text_snippet = (extracted_text or "").strip() or "(no text extracted)"
    if len(text_snippet) > 5000:
        text_snippet = text_snippet[:5000] + "\n... (truncated)"

    regex_json_str = json.dumps(regex_summary, ensure_ascii=False, indent=2)
    raw_snippet = (raw_text or "").strip() or "(empty response)"
    if len(raw_snippet) > 5000:
        raw_snippet = raw_snippet[:5000] + "\n... (truncated)"

    def js_escape(s: str) -> str:
        s = re.sub(r"(?i)</script>", "<\\/script>", s)
        return s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

    text_escaped = js_escape(text_snippet)
    regex_escaped = js_escape(regex_json_str)
    raw_escaped = js_escape(raw_snippet)

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


def render_job_result_html(
    kind: str, job_id: str, rows: int, columns: list[str]
) -> str:
    label_map = {
        "raster": "Raster",
        "vector": "Vector",
        "unified": "Unified",
        "e055": "E-055",
        "e251": "E-251",
        "e142": "E-142",
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


EQUIPMENT_TABLE_COLUMNS = ["器具記号", "メーカー", "相当型番"]
EQUIPMENT_TABLE_COLUMN_SOURCE_KEYS = {
    "e055": {
        "器具記号": ("器具記号", "機器器具"),
        "メーカー": ("メーカー",),
        "相当型番": ("相当型番", "型番"),
    },
    "e251": {
        "器具記号": ("器具記号", "機器器具"),
        "メーカー": ("メーカー",),
        "相当型番": ("相当型番", "型番"),
    },
}


def build_equipment_table_html(csv_path: Path, kind: str) -> str:
    """Build table HTML for e055 or e251 from CSV path."""
    rows = read_csv_dict_rows(csv_path)
    source_keys = EQUIPMENT_TABLE_COLUMN_SOURCE_KEYS.get(
        kind, EQUIPMENT_TABLE_COLUMN_SOURCE_KEYS["e055"]
    )
    columns = [
        {"key": col, "label": col, "hint": ""} for col in EQUIPMENT_TABLE_COLUMNS
    ]
    normalized_rows = []
    for row in rows:
        normalized_row = {}
        for column in EQUIPMENT_TABLE_COLUMNS:
            value = ""
            for source_key in source_keys[column]:
                if source_key in row:
                    value = str(row.get(source_key, "") or "")
                    break
            normalized_row[column] = value
        normalized_rows.append(normalized_row)
    return build_table_html(columns, normalized_rows)


def build_e142_rows_html(csv_path: Path) -> str:
    rows = read_csv_rows(csv_path)
    if not rows:
        return (
            '<div class="rounded border border-stone-300 bg-white px-4 py-6 text-center text-sm text-stone-500">'
            "データがありません"
            "</div>"
        )
    line_items = []
    for row in rows:
        row_buffer = io.StringIO()
        csv.writer(row_buffer).writerow(row)
        text = row_buffer.getvalue().rstrip("\r\n")
        line_items.append(
            '<li class="rounded border border-stone-200 bg-white px-3 py-2 font-mono text-xs text-ink-light">'
            f"{html.escape(text)}"
            "</li>"
        )
    return f"<ol class=\"space-y-2\">{''.join(line_items)}</ol>"


def render_extractor_success_html(
    kind: str, job_id: str, content_html: str, row_count: int
) -> str:
    safe_job_id = html.escape(job_id, quote=True)
    download_url = f"/jobs/{job_id}/{kind}.csv"
    safe_download_url = html.escape(download_url, quote=True)
    return f"""
    <section class="relative rounded-lg border border-emerald-300 bg-emerald-50 p-4 shadow-sm"
      data-status="success"
      data-kind="{html.escape(kind)}"
      data-job-id="{safe_job_id}">
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
      </a>
      <div class="text-sm font-semibold text-emerald-800">抽出が完了しました</div>
      <div class="mt-1 text-sm text-emerald-900">Rows: {int(row_count)}</div>
      <div class="mt-4 overflow-x-auto">{content_html}</div>
    </section>
    """


def render_extractor_error_html(kind: str, message: str) -> str:
    safe_message = html.escape(single_line_message(message), quote=True)
    return f"""
    <section class="rounded-lg border border-red-300 bg-red-50 p-4 shadow-sm" data-status="error" data-kind="{html.escape(kind)}">
      <div class="text-sm font-semibold text-red-800">処理に失敗しました</div>
      <div class="mt-1 text-sm">message: {safe_message}</div>
    </section>
    """
