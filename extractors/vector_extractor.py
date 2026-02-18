#!/usr/bin/env python3
"""Extract two top-side equipment tables from PDF into one XLSX file.

Extraction logic is PDF-only. The optional answer XLSX is used only for
post-extraction validation checks.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from xml.etree import ElementTree as ET

import pdfplumber


CELL_COUNT = 19
SPLIT_SUFFIX_PATTERN = re.compile(r"^-\d+$")
DRAWING_NO_PATTERN = re.compile(r"^[A-Z]{1,4}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*$")
DRAWING_NO_SEARCH_PATTERN = re.compile(r"[A-Z]{1,4}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*")


def normalize_cell(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    parts = [p.strip() for p in value.split("\n") if p.strip()]
    value = " ".join(parts)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def normalize_equipment_code(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", normalize_cell(value or ""))
    text = text.replace(" ", "").replace("　", "")
    text = text.replace("~", "～")
    text = text.replace("〜", "～")
    return text


def normalize_drawing_number_candidate(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", normalize_cell(value or "")).upper()
    text = text.replace(" ", "").replace("　", "")
    text = re.sub(r"[‐‑‒–—―ー−－]", "-", text)
    return text if DRAWING_NO_PATTERN.fullmatch(text) else ""


def _extract_drawing_candidates_from_text(text: str) -> List[str]:
    normalized = unicodedata.normalize("NFKC", text or "").upper()
    normalized = re.sub(r"[‐‑‒–—―ー−－]", "-", normalized)
    candidates: List[str] = []
    for matched in DRAWING_NO_SEARCH_PATTERN.findall(normalized):
        candidate = normalize_drawing_number_candidate(matched)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def extract_drawing_number_from_page(page: pdfplumber.page.Page) -> str:
    page_text = ""
    if hasattr(page, "extract_text"):
        page_text = str(page.extract_text() or "")
    if not page_text and hasattr(page, "crop"):
        page_text = str(page.crop((0, 0, page.width, page.height)).extract_text() or "")

    if page_text:
        for line in page_text.splitlines():
            line_norm = unicodedata.normalize("NFKC", line or "").replace(" ", "").replace("　", "")
            if "図面番号" not in line_norm and ("図面" not in line_norm or "番号" not in line_norm):
                continue
            candidates = _extract_drawing_candidates_from_text(line)
            if candidates:
                return candidates[0]

    if not hasattr(page, "extract_words"):
        return ""

    words = page.extract_words(
        x_tolerance=1,
        y_tolerance=1,
        keep_blank_chars=False,
        use_text_flow=True,
    )
    if not words:
        return ""

    bottom_right_candidates: List[str] = []
    for word in words:
        x0 = float(word.get("x0", 0.0))
        top = float(word.get("top", 0.0))
        if x0 < page.width * 0.65 or top < page.height * 0.65:
            continue
        candidate = normalize_drawing_number_candidate(str(word.get("text", "")))
        if candidate and candidate not in bottom_right_candidates:
            bottom_right_candidates.append(candidate)
    if bottom_right_candidates:
        return bottom_right_candidates[0]
    return ""


def looks_like_equipment_code(text: str) -> bool:
    # Examples: SF-P-1, EF-B2-3, F-1-2, CAV-3～6-1, OS-AH-1
    normalized = normalize_equipment_code(text)
    if not normalized:
        return False
    return bool(re.match(r"^(?=.*\d)[A-Z0-9～~]+(?:-[A-Z0-9～~]+)*$", normalized))


def has_note_marker(row: Sequence[str]) -> bool:
    joined = "".join(row)
    if "記 事" in joined or "注記事項" in joined or "特記事項" in joined:
        return True
    return any(normalize_cell(cell).startswith("■") for cell in row)


def dedupe_join(base: str, extra: str, sep: str = " / ") -> str:
    if not extra:
        return base
    if not base:
        return extra
    parts = [p.strip() for p in base.split(sep)]
    if extra in parts:
        return base
    return f"{base}{sep}{extra}"


def _normalize_summary_name(text: str) -> str:
    """Normalize known OCR/join artifacts in summary-left table names."""
    normalized = normalize_cell(text)
    if not normalized:
        return ""
    normalized = re.sub(
        r"ルームエアコ\s*マルチタイプン",
        "ルームエアコン マルチタイプ",
        normalized,
    )
    normalized = re.sub(r"ルームエアコ\s*ン", "ルームエアコン", normalized)
    return normalize_cell(normalized)


def _join_summary_name(base: str, extra: str) -> str:
    base_normalized = _normalize_summary_name(base)
    extra_normalized = _normalize_summary_name(extra)
    if not extra_normalized:
        return base_normalized
    if not base_normalized:
        return extra_normalized
    if extra_normalized in base_normalized:
        return base_normalized
    if base_normalized in extra_normalized:
        return extra_normalized
    return f"{base_normalized} {extra_normalized}".strip()


def _merge_record_value(
    current_value: str,
    incoming_value: str,
    *,
    col_index: int,
    summary_like: bool,
) -> str:
    if not incoming_value:
        return current_value
    if col_index == 15:
        # 台数/合計は単一値として扱う。継続行で複数値を連結しない。
        return current_value or incoming_value
    if summary_like and col_index == 1:
        return _join_summary_name(current_value, incoming_value)
    return dedupe_join(current_value, incoming_value)


def cluster_values(values: Iterable[float], tolerance: float) -> List[float]:
    sorted_values = sorted(values)
    clusters: List[List[float]] = []
    for v in sorted_values:
        if not clusters or abs(v - clusters[-1][-1]) > tolerance:
            clusters.append([v])
        else:
            clusters[-1].append(v)
    return [sum(c) / len(c) for c in clusters]


def pick_target_tables(page: pdfplumber.page.Page) -> List[pdfplumber.table.Table]:
    candidates = []
    for table in page.find_tables():
        x0, top, x1, bottom = table.bbox
        width = x1 - x0
        if width < page.width * 0.4:
            continue
        if bottom > page.height * 0.85:
            continue
        candidates.append(table)
    candidates.sort(key=lambda t: t.bbox[0])
    if not candidates:
        return []
    if len(candidates) != 2:
        info = [f"bbox={t.bbox}" for t in page.find_tables()]
        raise ValueError(
            f"Expected 2 target tables, found {len(candidates)}. "
            f"All detected tables: {info}"
        )
    return candidates


def collect_grid_lines(
    page: pdfplumber.page.Page, bbox: Tuple[float, float, float, float]
) -> Tuple[List[float], List[float]]:
    x0, top, x1, bottom = bbox
    width = x1 - x0
    height = bottom - top
    vertical_segments: List[Tuple[float, float, float]] = []
    horizontal_top_y: List[float] = []

    for line in page.lines:
        lx0, ly0, lx1, ly1 = line["x0"], line["y0"], line["x1"], line["y1"]
        if abs(lx1 - lx0) < 0.2:
            x = (lx0 + lx1) / 2
            line_top = page.height - max(ly0, ly1)
            line_bottom = page.height - min(ly0, ly1)
            if (
                x0 - 2 <= x <= x1 + 2
                and top - 2 <= line_top <= bottom + 2
                and top - 2 <= line_bottom <= bottom + 2
            ):
                vertical_segments.append((x, line_top, line_bottom))
        elif abs(ly1 - ly0) < 0.2:
            y_top = page.height - ly0
            length = abs(lx1 - lx0)
            left = min(lx0, lx1)
            right = max(lx0, lx1)
            if (
                x0 - 2 <= left <= x0 + 4
                and x1 - 4 <= right <= x1 + 2
                and length >= width * 0.95
                and top - 2 <= y_top <= bottom + 2
            ):
                horizontal_top_y.append(y_top)

    x_clusters = cluster_values((s[0] for s in vertical_segments), tolerance=0.6)
    vertical_x: List[float] = []
    for xc in x_clusters:
        cluster_segs = [s for s in vertical_segments if abs(s[0] - xc) <= 0.6]
        if not cluster_segs:
            continue
        min_top = min(s[1] for s in cluster_segs)
        max_bottom = max(s[2] for s in cluster_segs)
        span = max_bottom - min_top
        if span >= height * 0.7:
            vertical_x.append(xc)

    vertical = cluster_values(vertical_x, tolerance=0.6)
    horizontal = cluster_values(horizontal_top_y, tolerance=0.2)
    if len(vertical) != CELL_COUNT + 1:
        raise ValueError(
            f"Expected {CELL_COUNT + 1} vertical borders, got {len(vertical)} "
            f"for bbox={bbox}"
        )
    if len(horizontal) < 4:
        raise ValueError(f"Too few horizontal borders ({len(horizontal)}) for bbox={bbox}")
    return vertical, horizontal


def extract_grid_rows(
    page: pdfplumber.page.Page,
    vertical: List[float],
    horizontal: List[float],
) -> List[List[str]]:
    settings = {
        "vertical_strategy": "explicit",
        "horizontal_strategy": "explicit",
        "explicit_vertical_lines": vertical,
        "explicit_horizontal_lines": horizontal,
        "intersection_tolerance": 3,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "text_tolerance": 2,
    }
    rows = page.extract_table(settings)
    if not rows:
        raise ValueError("Table extraction produced no rows.")
    normalized: List[List[str]] = []
    for row in rows:
        current = [normalize_cell(c) for c in row[:CELL_COUNT]]
        if len(current) < CELL_COUNT:
            current.extend([""] * (CELL_COUNT - len(current)))
        normalized.append(current)
    return normalized


def assign_col(x_center: float, vertical: Sequence[float]) -> int | None:
    for i in range(len(vertical) - 1):
        if vertical[i] <= x_center < vertical[i + 1]:
            return i
    if abs(x_center - vertical[-1]) < 0.5:
        return len(vertical) - 2
    return None


def normalize_header_text(text: str) -> str:
    text = text.replace(" ", "").replace("　", "")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("＃", "#")
    return text


def _default_header_rows() -> List[List[str]]:
    header1 = [""] * CELL_COUNT
    header2 = [""] * CELL_COUNT

    header1[0] = "機器番号"
    header1[1] = "名称"
    header1[2] = "系統"
    header1[3] = "仕様"
    header1[8] = "動力 (50Hz)"
    header1[14] = "付属品・その他"
    header1[15] = "台数"
    header1[16] = "設置場所"
    header1[18] = "備考"

    header2[4] = "番手 #(φ)"
    header2[5] = "機器風量 m3/h"
    header2[6] = "静圧 Pa"
    header2[7] = "騒音値 (dB)"
    header2[8] = "相 P-V"
    header2[9] = "消費電力 (KW)"
    header2[10] = "始動方式"
    header2[11] = "操作"
    header2[12] = "監視"
    header2[13] = "種別"
    header2[16] = "階"
    header2[17] = "部屋名"
    header2[18] = "(参考型番)"
    return [header1, header2]


def _normalize_header_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return normalized.replace(" ", "").replace("　", "").replace("\n", "").strip()


def _build_header_by_col(rows: Sequence[Sequence[str]], header_depth: int) -> Dict[int, str]:
    max_cols = max((len(r) for r in rows), default=0)
    out: Dict[int, str] = {}
    for col_index in range(max_cols):
        blob = "".join(
            _normalize_header_for_match(rows[row_index][col_index] if col_index < len(rows[row_index]) else "")
            for row_index in range(min(header_depth, len(rows)))
        )
        out[col_index] = blob
    return out


def _is_summary_data_row(row: Sequence[str], id_col: int) -> bool:
    equipment_id = normalize_equipment_code(row[id_col] if id_col < len(row) else "")
    if looks_like_equipment_code(equipment_id):
        return True
    if equipment_id.endswith("-") and id_col + 1 < len(row):
        suffix = normalize_equipment_code(row[id_col + 1])
        if re.fullmatch(r"\d+", suffix):
            return True
    return False


def _detect_summary_header_depth(rows: Sequence[Sequence[str]], id_col: int, max_depth: int = 8) -> int:
    limit = min(max_depth, len(rows))
    for idx in range(limit):
        if _is_summary_data_row(rows[idx], id_col):
            return max(1, idx)
    return max(1, limit)


def _pick_col_from_headers(
    header_by_col: Dict[int, str], keywords: Sequence[str], *, exclude_keywords: Sequence[str] = ()
) -> int | None:
    normalized_keywords = [_normalize_header_for_match(k) for k in keywords]
    normalized_excludes = [_normalize_header_for_match(k) for k in exclude_keywords]
    for col_index, header_blob in header_by_col.items():
        if any(keyword and keyword in header_blob for keyword in normalized_keywords):
            if any(ex and ex in header_blob for ex in normalized_excludes):
                continue
            return col_index
    return None


def _pick_summary_left_tables(page: pdfplumber.page.Page) -> List[pdfplumber.table.Table]:
    if not hasattr(page, "find_tables"):
        return []

    candidates: List[pdfplumber.table.Table] = []
    for table in page.find_tables():
        x0, top, x1, bottom = table.bbox
        width_ratio = (x1 - x0) / page.width
        if width_ratio < 0.45:
            continue
        if top > page.height * 0.25:
            continue
        if bottom > page.height * 0.95:
            continue

        raw_rows = table.extract() or []
        normalized_rows = [[normalize_cell(c) for c in row] for row in raw_rows]
        id_col = _pick_col_from_headers(_build_header_by_col(normalized_rows, header_depth=4), ["機器番号", "記号"])
        if id_col is None:
            continue
        header_depth = _detect_summary_header_depth(normalized_rows, id_col=id_col)
        header_by_col = _build_header_by_col(normalized_rows, header_depth=header_depth)
        name_col = _pick_col_from_headers(header_by_col, ["名称"])
        total_col = _pick_col_from_headers(header_by_col, ["合計"])
        if name_col is None or total_col is None:
            continue
        candidates.append(table)

    candidates.sort(key=lambda t: t.bbox[0])
    return candidates[:1]


def _extract_rows_from_summary_left_table(
    page: pdfplumber.page.Page, table: pdfplumber.table.Table
) -> List[List[str]]:
    raw_rows = table.extract()
    if not raw_rows:
        raise ValueError("Summary-left table extraction produced no rows.")

    max_cols = max(len(r) for r in raw_rows)
    normalized_rows: List[List[str]] = []
    for row in raw_rows:
        cells = [normalize_cell(c) for c in row]
        if len(cells) < max_cols:
            cells.extend([""] * (max_cols - len(cells)))
        normalized_rows.append(cells)

    id_col = _pick_col_from_headers(_build_header_by_col(normalized_rows, header_depth=4), ["機器番号", "記号"])
    if id_col is None:
        raise ValueError("Could not resolve summary-left required id column.")
    header_depth = _detect_summary_header_depth(normalized_rows, id_col=id_col)
    header_by_col = _build_header_by_col(normalized_rows, header_depth=header_depth)
    name_col = _pick_col_from_headers(header_by_col, ["名称"])
    total_col = _pick_col_from_headers(header_by_col, ["合計"])
    spec_col = _pick_col_from_headers(header_by_col, ["仕様"])
    if name_col is None or total_col is None:
        raise ValueError(
            "Could not resolve summary-left required columns: "
            f"id={id_col}, name={name_col}, total={total_col}"
        )

    name_cols = [name_col]
    for col in range(name_col + 1, max_cols):
        if spec_col is not None and col >= spec_col:
            break
        if header_by_col.get(col, "") == "":
            name_cols.append(col)
        else:
            break

    projected_rows: List[List[str]] = []
    for row in normalized_rows:
        projected = [""] * CELL_COUNT

        equipment_id = normalize_equipment_code(row[id_col])
        if equipment_id.endswith("-") and id_col + 1 < len(row):
            suffix = normalize_equipment_code(row[id_col + 1])
            if re.fullmatch(r"\d+", suffix):
                equipment_id = f"{equipment_id}{suffix}"

        name = "".join(normalize_cell(row[col]) for col in name_cols if col < len(row)).strip()
        name = _normalize_summary_name(name)
        total = normalize_cell(row[total_col]) if total_col < len(row) else ""

        projected[0] = equipment_id
        projected[1] = name
        projected[9] = ""
        projected[15] = total
        projected_rows.append(projected)
    return projected_rows


def _pick_power_col(header_by_col: Dict[int, str]) -> int | None:
    exact_candidates: List[Tuple[int, str]] = []
    broad_candidates: List[Tuple[int, str]] = []

    for col_index, header_blob in header_by_col.items():
        if "消費電力" not in header_blob:
            continue
        broad_candidates.append((col_index, header_blob))
        if "出力" not in header_blob:
            exact_candidates.append((col_index, header_blob))

    target = exact_candidates or broad_candidates
    if not target:
        return None

    # Prefer the most specific header text (usually "消費電力(KW)") over merged blocks.
    target.sort(key=lambda t: len(t[1]))
    return target[0][0]


def _score_power_candidate(current_value: str, candidate: str) -> int:
    current_norm = _normalize_header_for_match(current_value)
    candidate_norm = _normalize_header_for_match(candidate)
    if not candidate_norm or not re.search(r"\d", candidate_norm):
        return -1
    if not current_norm:
        return -1

    score = 0
    if candidate_norm == current_norm:
        score += 100
    elif candidate_norm.startswith(current_norm):
        score += 80 + len(candidate_norm)
    elif current_norm.startswith(candidate_norm):
        score += 40 + len(candidate_norm)

    current_prefix = re.split(r"\d", current_norm, maxsplit=1)[0]
    candidate_prefix = re.split(r"\d", candidate_norm, maxsplit=1)[0]
    if current_prefix and current_prefix == candidate_prefix:
        score += 20
    if "." in candidate_norm:
        score += 1
    return score


def _select_power_value_candidate(current_value: str, candidates: Sequence[str]) -> str:
    best_value = current_value
    best_score = 0
    for candidate in candidates:
        score = _score_power_candidate(current_value, candidate)
        if score > best_score:
            best_value = candidate
            best_score = score
    return best_value


def _extract_power_candidates_from_bbox(
    page: pdfplumber.page.Page, bbox: Tuple[float, float, float, float]
) -> List[str]:
    crop = page.crop(bbox)
    lines: List[str] = []

    text = crop.extract_text() or ""
    if text:
        for line in text.splitlines():
            normalized = normalize_cell(line)
            if normalized:
                lines.append(normalized)

    if not lines:
        words = crop.extract_words(
            x_tolerance=1,
            y_tolerance=1,
            keep_blank_chars=False,
            use_text_flow=True,
        )
        for word in words:
            normalized = normalize_cell(str(word.get("text", "")))
            if normalized:
                lines.append(normalized)

    deduped: List[str] = []
    for line in lines:
        if line not in deduped:
            deduped.append(line)
    return deduped


def _extract_rows_via_table_cells(
    page: pdfplumber.page.Page, table: pdfplumber.table.Table
) -> List[List[str]]:
    raw_rows = table.extract()
    if not raw_rows:
        raise ValueError("Table extraction fallback produced no rows.")

    max_cols = max(len(r) for r in raw_rows)
    normalized_rows: List[List[str]] = []
    for row in raw_rows:
        cells = [normalize_cell(c) for c in row]
        if len(cells) < max_cols:
            cells.extend([""] * (max_cols - len(cells)))
        normalized_rows.append(cells)

    header_by_col = _build_header_by_col(normalized_rows, header_depth=8)
    id_col = _pick_col_from_headers(header_by_col, ["機器番号", "記号"])
    name_col = _pick_col_from_headers(header_by_col, ["名称"])
    power_col = _pick_power_col(header_by_col)
    count_col = _pick_col_from_headers(header_by_col, ["台数", "数量"])
    if id_col is None or name_col is None or power_col is None or count_col is None:
        raise ValueError(
            "Could not resolve required columns from fallback table headers: "
            f"id={id_col}, name={name_col}, power={power_col}, count={count_col}"
        )

    col_bounds: Dict[int, Tuple[float, float]] = {}
    for col_index in range(max_cols):
        x0_list: List[float] = []
        x1_list: List[float] = []
        for table_row in table.rows:
            if col_index >= len(table_row.cells):
                continue
            cell = table_row.cells[col_index]
            if cell is None:
                continue
            x0_list.append(float(cell[0]))
            x1_list.append(float(cell[2]))
        if x0_list and x1_list:
            col_bounds[col_index] = (min(x0_list), max(x1_list))

    projected_rows: List[List[str]] = []
    for row_index, row in enumerate(normalized_rows):
        projected = [""] * CELL_COUNT
        projected[0] = row[id_col] if id_col < len(row) else ""
        projected[1] = row[name_col] if name_col < len(row) else ""
        projected[9] = row[power_col] if power_col < len(row) else ""
        projected[15] = row[count_col] if count_col < len(row) else ""

        note_marker_text = ""
        for cell in row:
            normalized = normalize_cell(cell)
            if normalized.startswith("■"):
                note_marker_text = normalized
                break
        if note_marker_text:
            # Keep note marker in a projected column so extract_records can stop.
            projected[3] = note_marker_text

        if (
            projected[9]
            and power_col in col_bounds
            and row_index < len(table.rows)
            and table.rows[row_index].bbox is not None
        ):
            row_bbox = table.rows[row_index].bbox
            px0, px1 = col_bounds[power_col]
            bbox = (px0, float(row_bbox[1]), px1, float(row_bbox[3]))
            candidates = _extract_power_candidates_from_bbox(page, bbox)
            projected[9] = _select_power_value_candidate(projected[9], candidates)

        projected_rows.append(projected)
    return projected_rows

def reconstruct_headers_from_pdf(
    page: pdfplumber.page.Page,
    bbox: Tuple[float, float, float, float],
    vertical: Sequence[float],
) -> Tuple[List[str], List[str]]:
    words = page.crop(bbox).extract_words(
        x_tolerance=1,
        y_tolerance=1,
        keep_blank_chars=False,
        use_text_flow=True,
    )
    header_words: List[Dict[str, float | str]] = []
    first_code_top: float | None = None
    for w in words:
        text = normalize_cell(str(w["text"]))
        if not text:
            continue
        if looks_like_equipment_code(text):
            top = float(w["top"])
            first_code_top = top if first_code_top is None else min(first_code_top, top)

    if first_code_top is None:
        raise ValueError("Could not detect first equipment code for header boundary.")

    for w in words:
        text = normalize_cell(str(w["text"]))
        if not text:
            continue
        if "換気機器表" in text:
            continue
        if float(w["top"]) < first_code_top - 1.0:
            header_words.append(w)

    if not header_words:
        raise ValueError("No header words detected in target table.")

    lines: List[Dict[str, object]] = []
    for w in sorted(header_words, key=lambda x: (float(x["top"]), float(x["x0"]))):
        cy = (float(w["top"]) + float(w["bottom"])) / 2
        if not lines or abs(cy - float(lines[-1]["cy"])) > 2.5:
            lines.append({"cy": cy, "words": [w]})
        else:
            words_in_line = lines[-1]["words"]
            assert isinstance(words_in_line, list)
            words_in_line.append(w)
            lines[-1]["cy"] = (float(lines[-1]["cy"]) + cy) / 2

    if len(lines) < 3:
        raise ValueError(f"Expected at least 3 header text lines, got {len(lines)}")

    group_line = lines[0]["words"]
    rest = [line["words"] for line in lines[1:]]
    if len(rest) == 2:
        sub_line = rest[0]
        unit_line = rest[1]
    else:
        # Some PDFs split "始動方式" into an extra line; keep only the last line
        # as the unit row and merge the middle lines into sub headers.
        sub_line = []
        for segment in rest[:-1]:
            assert isinstance(segment, list)
            sub_line.extend(segment)
        unit_line = rest[-1]
    assert isinstance(group_line, list)
    assert isinstance(sub_line, list)
    assert isinstance(unit_line, list)

    def build_by_col(line_words: List[Dict[str, object]]) -> Dict[int, str]:
        col_tokens: Dict[int, List[Tuple[float, str]]] = defaultdict(list)
        for w in line_words:
            x0 = float(w["x0"])
            x1 = float(w["x1"])
            cx = (x0 + x1) / 2
            col = assign_col(cx, vertical)
            if col is None:
                continue
            col_tokens[col].append((x0, normalize_header_text(str(w["text"]))))
        out: Dict[int, str] = {}
        for c, vals in col_tokens.items():
            vals.sort(key=lambda t: t[0])
            out[c] = "".join(v for _, v in vals)
        return out

    g = build_by_col(group_line)
    s = build_by_col(sub_line)
    u = build_by_col(unit_line)

    header1 = [""] * CELL_COUNT
    header2 = [""] * CELL_COUNT

    header1[0] = g.get(0, "機器番号")
    header1[1] = g.get(1, "名称")
    header1[2] = g.get(2, "系統")
    header1[3] = "".join(g.get(i, "") for i in range(3, 8)) or "仕様"
    header1[8] = "".join(g.get(i, "") for i in range(8, 14)) or "動力(50Hz)"
    header1[14] = g.get(14, "付属品・その他")
    header1[15] = g.get(15, "台数")
    header1[16] = "".join(g.get(i, "") for i in (16, 17)) or "設置場所"
    header1[18] = "".join(g.get(i, "") for i in (18,)) or "備考"

    header2[3] = s.get(3, "型式")
    header2[4] = f"{s.get(4, '番手')} {u.get(4, '#(φ)')}".strip()
    header2[5] = f"{s.get(5, '機器風量')} {u.get(5, 'm3/h')}".strip()
    header2[6] = f"{s.get(6, '静圧')} {u.get(6, 'Pa')}".strip()
    header2[7] = f"{s.get(7, '騒音値')} {u.get(7, '(dB)')}".strip()
    header2[8] = f"{s.get(8, '相')} {u.get(8, 'P-V')}".strip()
    header2[9] = f"{s.get(9, '消費電力')} {u.get(9, '(KW)')}".strip()
    start_mode = s.get(10, "始動方式")
    if start_mode and "方式" not in start_mode:
        start_mode = f"{start_mode}方式"
    header2[10] = start_mode or "始動方式"
    header2[11] = s.get(11, "操作")
    header2[12] = s.get(12, "監視")
    header2[13] = s.get(13, "種別")
    header2[16] = s.get(16, "階")
    header2[17] = "".join(s.get(i, "") for i in (17, 18)) or "部屋名"
    header2[18] = u.get(18, "(参考型番)")

    # Canonical normalization from PDF-reconstructed fragments.
    header1[3] = "仕様"
    header1[8] = "動力 (50Hz)"
    header1[16] = "設置場所"
    header1[18] = "備考"
    header2[4] = "番手 #(φ)"
    header2[5] = "機器風量 m3/h"
    header2[6] = "静圧 Pa"
    header2[7] = "騒音値 (dB)"
    header2[8] = "相 P-V"
    header2[9] = "消費電力 (KW)"
    header2[18] = "(参考型番)"

    return header1, header2


def extract_records(rows: Sequence[Sequence[str]]) -> Tuple[List[List[str]], int]:
    records: List[List[str]] = []
    current: List[str] | None = None
    note_row_count = 0

    for raw in rows:
        row = [normalize_cell(c) for c in raw[:CELL_COUNT]]
        if len(row) < CELL_COUNT:
            row.extend([""] * (CELL_COUNT - len(row)))
        if not any(row):
            continue

        key = normalize_equipment_code(row[0])
        if looks_like_equipment_code(key):
            # Some PDFs split the trailing sequence number into the "名称" cell.
            # Rebuild equipment id like "CAV-11～15" + "-1" -> "CAV-11～15-1".
            if SPLIT_SUFFIX_PATTERN.match(row[1]):
                key = f"{key}{row[1]}"
                row[1] = ""
            row[0] = key
            if current is not None and key == current[0]:
                # Some formats repeat the same equipment id across multi-line blocks.
                # Treat it as continuation instead of starting a new record.
                summary_like = (not normalize_cell(current[9])) and (not normalize_cell(row[9]))
                for i, value in enumerate(row):
                    if i == 0:
                        continue
                    current[i] = _merge_record_value(
                        current[i],
                        value,
                        col_index=i,
                        summary_like=summary_like,
                    )
                continue
            if current is not None:
                records.append(current)
            current = row.copy()
            continue

        if current is None:
            continue

        if has_note_marker(row):
            note_row_count += 1
            break

        summary_like = (not normalize_cell(current[9])) and (not normalize_cell(row[9]))
        for i, value in enumerate(row):
            if i == 0:
                # Continuation rows can carry truncated ids like "PAC-1-".
                # Never merge them into the canonical equipment id.
                continue
            current[i] = _merge_record_value(
                current[i],
                value,
                col_index=i,
                summary_like=summary_like,
            )

    if current is not None:
        records.append(current)
    return records, note_row_count


def excel_col_name(index: int) -> str:
    # 0-based to Excel column name.
    n = index + 1
    chars = []
    while n:
        n, r = divmod(n - 1, 26)
        chars.append(chr(ord("A") + r))
    return "".join(reversed(chars))


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_sheet_xml(rows: Sequence[Sequence[str]]) -> str:
    max_row = len(rows)
    dim = f"A1:{excel_col_name(CELL_COUNT - 1)}{max_row}"
    row_xml: List[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: List[str] = []
        for c_idx, value in enumerate(row):
            if value == "":
                continue
            ref = f"{excel_col_name(c_idx)}{r_idx}"
            safe = xml_escape(value)
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>')
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    merges = [
        "A1:A2",
        "B1:B2",
        "C1:C2",
        "D1:H1",
        "I1:N1",
        "O1:O2",
        "P1:P2",
        "Q1:R1",
    ]
    merge_xml = "".join(f'<mergeCell ref="{m}"/>' for m in merges)

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dim}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        '<cols><col min="1" max="19" width="15" customWidth="1"/></cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<mergeCells count="{len(merges)}">{merge_xml}</mergeCells>'
        '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
        'header="0.3" footer="0.3"/>'
        "</worksheet>"
    )


def write_xlsx(path: Path, rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_xml = build_sheet_xml(rows)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="換気機器表" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def write_csv(path: Path, rows: Sequence[Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def build_single_header_csv_rows(rows: Sequence[Sequence[str]]) -> List[List[str]]:
    if len(rows) < 3:
        raise ValueError("Need at least 2 header rows and 1 data row for flat CSV.")

    h1 = list(rows[0][:CELL_COUNT])
    h2 = list(rows[1][:CELL_COUNT])
    data_rows = [list(r[:CELL_COUNT]) for r in rows[2:]]

    # Forward-fill merged parent headers (e.g., 仕様, 動力 (50Hz), 設置場所).
    parent = []
    current = ""
    for cell in h1:
        if cell:
            current = cell
        parent.append(current)

    flat_header: List[str] = []
    for i in range(CELL_COUNT):
        p = parent[i].strip()
        c = h2[i].strip()
        if p and c:
            flat_header.append(f"{p}_{c}")
        else:
            flat_header.append(p or c)

    return [flat_header] + data_rows


def build_four_column_rows(
    rows: Sequence[Sequence[str]], drawing_numbers: Sequence[str] | None = None
) -> List[List[str]]:
    if len(rows) < 3:
        raise ValueError("Need at least 2 header rows and 1 data row for 4-column CSV.")
    data_rows = [list(r[:CELL_COUNT]) for r in rows[2:]]
    if drawing_numbers is not None and len(drawing_numbers) != len(data_rows):
        raise ValueError(
            "Length mismatch: drawing_numbers must align with data rows "
            f"({len(drawing_numbers)} != {len(data_rows)})."
        )
    header = ["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数"]
    if drawing_numbers is not None:
        header.append("図面番号")
    out = [header]
    for index, r in enumerate(data_rows):
        row = [r[0], r[1], r[9], r[15]]
        if drawing_numbers is not None:
            row.append(drawing_numbers[index])
        out.append(row)
    return out


def read_xlsx_rows(path: Path, max_row: int, max_col: int) -> List[List[str]]:
    ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as z:
        sst: List[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                sst.append("".join((t.text or "") for t in si.findall(".//a:t", ns)))

        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        first_sheet = workbook.find("a:sheets", ns).find("a:sheet", ns)  # type: ignore[union-attr]
        rid = first_sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")  # type: ignore[union-attr]
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rels:
            if rel.get("Id") == rid:
                target = "xl/" + rel.get("Target", "").lstrip("/")
                break
        if target is None:
            raise ValueError("Could not resolve worksheet path from workbook rels.")

        ws = ET.fromstring(z.read(target))
        rows: List[List[str]] = [[""] * max_col for _ in range(max_row)]
        for row in ws.findall(".//a:sheetData/a:row", ns):
            r_idx = int(row.get("r", "0")) - 1
            if not (0 <= r_idx < max_row):
                continue
            for cell in row.findall("a:c", ns):
                ref = cell.get("r", "")
                m = re.match(r"([A-Z]+)(\d+)", ref)
                if not m:
                    continue
                col_letters = m.group(1)
                c_idx = 0
                for ch in col_letters:
                    c_idx = c_idx * 26 + ord(ch) - 64
                c_idx -= 1
                if not (0 <= c_idx < max_col):
                    continue

                value = ""
                cell_type = cell.get("t")
                if cell_type == "inlineStr":
                    t = cell.find("a:is/a:t", ns)
                    value = t.text if t is not None and t.text is not None else ""
                else:
                    v = cell.find("a:v", ns)
                    if v is not None and v.text is not None:
                        value = v.text
                        if cell_type == "s" and value.isdigit():
                            idx = int(value)
                            if 0 <= idx < len(sst):
                                value = sst[idx]

                rows[r_idx][c_idx] = normalize_cell(value)
    return rows


def validate_headers(actual_header: Sequence[Sequence[str]], expected_xlsx: Path) -> bool:
    expected = read_xlsx_rows(expected_xlsx, max_row=2, max_col=CELL_COUNT)
    diffs: List[str] = []
    for r in range(2):
        for c in range(CELL_COUNT):
            a = normalize_cell(actual_header[r][c])
            e = normalize_cell(expected[r][c])
            if a != e:
                diffs.append(f"{excel_col_name(c)}{r+1}: actual='{a}' expected='{e}'")

    if not diffs:
        print(f"Header validation: PASS ({expected_xlsx})")
        return True

    print(f"Header validation: FAIL ({expected_xlsx})")
    for d in diffs:
        print(f"  - {d}")
    return False


def extract_pdf_to_rows(
    pdf_path: Path,
    *,
    include_record_page_indexes: bool = False,
    include_page_drawing_numbers: bool = False,
) -> (
    Tuple[List[List[str]], int, List[List[str]]]
    | Tuple[List[List[str]], int, List[List[str]], List[int]]
    | Tuple[List[List[str]], int, List[List[str]], List[int], Dict[int, str]]
):
    if include_page_drawing_numbers and not include_record_page_indexes:
        raise ValueError("include_page_drawing_numbers requires include_record_page_indexes=True")

    with pdfplumber.open(str(pdf_path)) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages.")

        merged_records: List[List[str]] = []
        note_rows_total = 0
        header_rows: List[List[str]] | None = None
        record_page_indexes: List[int] = []
        drawing_by_page: Dict[int, str] = {}

        for page_index, page in enumerate(pdf.pages):
            target_tables = pick_target_tables(page)
            if target_tables:
                tables_to_process = target_tables
                use_summary_left = False
            else:
                tables_to_process = _pick_summary_left_tables(page)
                use_summary_left = True

            if not tables_to_process:
                continue

            for table in tables_to_process:
                bbox = table.bbox
                used_fallback = False
                if use_summary_left:
                    rows = _extract_rows_from_summary_left_table(page, table)
                else:
                    try:
                        vertical, horizontal = collect_grid_lines(page, bbox)
                        rows = extract_grid_rows(page, vertical, horizontal)
                    except ValueError:
                        rows = _extract_rows_via_table_cells(page, table)
                        used_fallback = True
                if header_rows is None:
                    if use_summary_left or used_fallback:
                        header_rows = _default_header_rows()
                    else:
                        h1, h2 = reconstruct_headers_from_pdf(page, bbox, vertical)
                        header_rows = [h1, h2]
                records, note_rows = extract_records(rows)
                note_rows_total += note_rows
                merged_records.extend(records)
                if include_record_page_indexes:
                    record_page_indexes.extend([page_index] * len(records))
                    if include_page_drawing_numbers and records and page_index not in drawing_by_page:
                        drawing_by_page[page_index] = extract_drawing_number_from_page(page)

        if header_rows is None:
            raise ValueError("No target tables found in any PDF page.")
        final_rows = header_rows + merged_records
        if include_record_page_indexes:
            if include_page_drawing_numbers:
                return final_rows, note_rows_total, header_rows, record_page_indexes, drawing_by_page
            return final_rows, note_rows_total, header_rows, record_page_indexes
        return final_rows, note_rows_total, header_rows


def extract_vector_pdf_four_columns(pdf_path: Path, out_csv_path: Path) -> Dict[str, object]:
    """Extract vector PDF table and write a fixed CSV for unified merge."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"Input PDF not found: {pdf_path}")

    rows, note_rows, _, record_page_indexes, drawing_by_page = extract_pdf_to_rows(
        pdf_path,
        include_record_page_indexes=True,
        include_page_drawing_numbers=True,
    )
    drawing_numbers = [drawing_by_page.get(page_index, "") for page_index in record_page_indexes]
    four_rows = build_four_column_rows(rows, drawing_numbers=drawing_numbers)
    write_csv(out_csv_path, four_rows)

    columns = four_rows[0] if four_rows else []
    return {
        "rows": max(0, len(four_rows) - 1),
        "columns": columns,
        "note_rows": note_rows,
        "output_csv": str(out_csv_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract two top-side PDF tables into merged XLSX/CSV."
    )
    parser.add_argument(
        "pdf",
        type=Path,
        nargs="?",
        default=Path("./data/機器表1.pdf"),
        help="Input PDF path (default: ./data/機器表1.pdf)",
    )
    parser.add_argument(
        "--output-xlsx",
        type=Path,
        default=Path("./data/ventilation_equipment_merged.xlsx"),
        help="Output XLSX path (default: ./data/ventilation_equipment_merged.xlsx)",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("./data/ventilation_equipment_merged.csv"),
        help="Output CSV path with original 2-row header (default: ./data/ventilation_equipment_merged.csv)",
    )
    parser.add_argument(
        "--output-csv-flat",
        type=Path,
        default=Path("./data/ventilation_equipment_merged_flat.csv"),
        help="Output CSV path with 1-row readable header (default: ./data/ventilation_equipment_merged_flat.csv)",
    )
    parser.add_argument(
        "--four-column",
        action="store_true",
        help="Also output a unified CSV (機器番号, 名称, 動力(50Hz)_消費電力, 台数, 図面番号).",
    )
    parser.add_argument(
        "--output-csv-four",
        type=Path,
        default=None,
        help="Output path for 4-column CSV. If omitted with --four-column, defaults to ./data/ventilation_equipment_four_columns.csv",
    )
    parser.add_argument(
        "--validate-against-xlsx",
        type=Path,
        default=None,
        help="Optional answer XLSX path for header validation only.",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        raise FileNotFoundError(f"Input PDF not found: {args.pdf}")
    if args.validate_against_xlsx and not args.validate_against_xlsx.exists():
        raise FileNotFoundError(
            f"Validation XLSX not found: {args.validate_against_xlsx}"
        )

    rows, note_rows, headers = extract_pdf_to_rows(args.pdf)
    write_xlsx(args.output_xlsx, rows)
    if args.output_csv:
        write_csv(args.output_csv, rows)
    if args.output_csv_flat:
        flat_rows = build_single_header_csv_rows(rows)
        write_csv(args.output_csv_flat, flat_rows)
    output_csv_four = args.output_csv_four
    if args.four_column and output_csv_four is None:
        output_csv_four = Path("./data/ventilation_equipment_four_columns.csv")
    if output_csv_four:
        four_rows = build_four_column_rows(rows)
        write_csv(output_csv_four, four_rows)

    print(f"Output: {args.output_xlsx.resolve()}")
    if args.output_csv:
        print(f"Output CSV: {args.output_csv.resolve()}")
    if args.output_csv_flat:
        print(f"Output CSV (flat header): {args.output_csv_flat.resolve()}")
    if output_csv_four:
        print(f"Output CSV (4 columns): {output_csv_four.resolve()}")
    print(f"Rows written: {len(rows)} (header=2, data={len(rows)-2})")
    print(f"Note rows captured in data region: {note_rows}")

    if args.validate_against_xlsx:
        ok = validate_headers(headers, args.validate_against_xlsx)
        if not ok:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
