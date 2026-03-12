"""M-E-Check customer table: column definitions, normalization, HTML building."""

import html
import unicodedata
from pathlib import Path
from typing import Optional

from app.core.utils import parse_float_or_none, single_line_message
from extractors.csv_utils import read_csv_dict_rows

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


def normalize_header_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return normalized.replace(" ", "").replace("　", "")


def pick_first_column_value(row: dict[str, str], candidates: list[str]) -> str:
    for key in candidates:
        value = row.get(key)
        if value is not None:
            return str(value)
    normalized_map = {normalize_header_token(k): v for k, v in row.items()}
    for key in candidates:
        value = normalized_map.get(normalize_header_token(key))
        if value is not None:
            return str(value)
    return ""


def normalize_judgment(value: str) -> str:
    text = str(value or "").strip()
    if text in {"一致", "○", "◯", "match"}:
        return "◯"
    if text in {"不一致", "×", "✗", "mismatch"}:
        return "✗"
    if text in {"判定不可", "要確認", "review"}:
        return "要確認"
    return text


def normalize_id_match_symbol(value: str) -> str:
    normalized = normalize_judgment(value)
    if normalized == "◯":
        return "○"
    if normalized == "✗":
        return "×"
    return normalized


def map_customer_summary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    mapped_rows: list[dict[str, str]] = []
    for row in rows:
        mapped_row: dict[str, str] = {}
        for label, candidates in CUSTOMER_SUMMARY_COLUMNS:
            value = pick_first_column_value(row, candidates)
            if label in CUSTOMER_SUMMARY_JUDGMENT_COLUMNS:
                value = normalize_judgment(value)
            mapped_row[label] = value
        mapped_rows.append(mapped_row)
    return mapped_rows


def map_customer_display_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    mapped_rows: list[dict[str, str]] = []
    for row in rows:
        mapped_row: dict[str, str] = {}
        for key, _label, candidates in _customer_display_leaf_columns():
            value = pick_first_column_value(row, candidates)
            if key == "ID照合":
                value = normalize_id_match_symbol(value)
            mapped_row[key] = value
        mapped_rows.append(mapped_row)
    return mapped_rows


def build_customer_table_header_html() -> str:
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


def compute_customer_summary(
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
        judgment = normalize_judgment(row.get("総合判定", ""))
        id_match = normalize_judgment(row.get("機器ID照合", ""))
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


def build_customer_summary_html(
    mapped_rows: list[dict[str, str]],
    *,
    vector_row_count: Optional[int] = None,
    raster_row_count: Optional[int] = None,
) -> str:
    summary = compute_customer_summary(
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


def build_customer_table_html(
    unified_csv_path: Path,
    *,
    vector_row_count: Optional[int] = None,
    raster_row_count: Optional[int] = None,
) -> str:
    rows = read_csv_dict_rows(unified_csv_path)
    diff_note_html = (
        f'<p class="mt-2 text-xs text-stone-600">{html.escape(DIFF_NOTE_TEXT)}</p>'
    )
    header_html = build_customer_table_header_html()
    display_columns = _customer_display_leaf_columns()
    display_column_count = len(display_columns)
    summary_rows = map_customer_summary_rows(rows)
    display_rows = map_customer_display_rows(rows)

    if not rows:
        summary_html = build_customer_summary_html(
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

    summary_html = build_customer_summary_html(
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


def render_customer_success_html(unified_job_id: str, table_html: str) -> str:
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


def render_customer_error_html(stage: str, message: str) -> str:
    safe_stage = html.escape(stage, quote=True)
    safe_message = html.escape(single_line_message(message), quote=True)
    return f"""
    <section class="rounded-lg border border-red-300 bg-red-50 p-4 shadow-sm"
      data-status="error"
      data-stage="{safe_stage}">
      <div class="text-sm font-semibold text-red-800">処理に失敗しました</div>
      <div class="mt-1 text-sm">stage: <code class="font-mono">{safe_stage}</code></div>
      <div class="mt-1 text-sm">message: {safe_message}</div>
    </section>
    """
