from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
from time import perf_counter
import unicodedata
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Tuple

from PIL import Image
import pdfplumber

try:
    import cv2  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency at runtime
    cv2 = None

try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional dependency at runtime
    np = None

from extractors.raster_extractor import (
    build_vision_client,
    count_pdf_pages,
    resolve_target_pages,
    run_pdftoppm,
    vision,
)

OUTPUT_COLUMNS = ["器具記号", "メーカー", "相当型番"]
OUTPUT_COLUMN_SOURCE_KEYS = {
    "器具記号": ("器具記号", "機器器具"),
    "メーカー": ("メーカー",),
    "相当型番": ("相当型番", "型番"),
}
MODEL_PATTERN = re.compile(r"\b([A-Z]{2,}(?:\s*-\s*[A-Z0-9]{1,20})+)\b")
MODEL_MULTIPLIER_SUFFIX_PATTERN = re.compile(r"\s*(?:\(\s*[xX×✕]\s*\d+\s*\)|[xX×✕]\s*\d+)")  # noqa: RUF001
COLON_MODEL_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9&._-]{1,30})\s*[:：]\s*([A-Z]{2,}(?:\s*-\s*[A-Z0-9]{1,20})+)")  # noqa: RUF001
DASH_VARIANTS_PATTERN = re.compile(r"[ー―−–—‐ｰ－]")  # noqa: RUF001
EXCLUDED_EMERGENCY_CODES = {"EDL", "EDM", "ECL", "ECM", "ECH", "ES1", "ES2"}
DEFAULT_DEBUG_FOCUS_TERMS = ("TP1", "TP2", "CT2G", "DL9", "同上", "TAD-", "LZD-")
LINE_ASSIST_MODE_ALLOWED = {"auto", "off", "force"}
LINE_ASSIST_DEFAULT_MODE = "auto"
LINE_ASSIST_DEFAULT_LATENCY_BUDGET_MS = 300
LINE_ASSIST_DEFAULT_MIN_CONFIDENCE = 0.70


@dataclass(frozen=True)
class LineAssistConfig:
    mode: str
    latency_budget_ms: int
    min_confidence: float
    debug_enabled: bool


@dataclass
class WordBox:
    text: str
    cx: float
    cy: float
    bbox: Tuple[float, float, float, float]


@dataclass
class RowCluster:
    row_y: float
    words: List[WordBox]


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "")


def compact_text(value: str) -> str:
    return normalize_text(value).replace(" ", "").replace("　", "")


def _is_truthy_env(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _line_assist_mode() -> str:
    raw = os.getenv("E055_LINE_ASSIST_MODE", LINE_ASSIST_DEFAULT_MODE).strip().lower()
    return raw if raw in LINE_ASSIST_MODE_ALLOWED else LINE_ASSIST_DEFAULT_MODE


def _line_assist_config() -> LineAssistConfig:
    mode = _line_assist_mode()
    latency_budget_ms = max(_int_env("E055_LINE_ASSIST_LATENCY_BUDGET_MS", LINE_ASSIST_DEFAULT_LATENCY_BUDGET_MS), 1)
    min_confidence = _float_env("E055_LINE_ASSIST_MIN_CONFIDENCE", LINE_ASSIST_DEFAULT_MIN_CONFIDENCE)
    min_confidence = min(max(min_confidence, 0.0), 1.0)
    debug_enabled = _is_truthy_env("E055_LINE_ASSIST_DEBUG")
    return LineAssistConfig(
        mode=mode,
        latency_budget_ms=latency_budget_ms,
        min_confidence=min_confidence,
        debug_enabled=debug_enabled,
    )


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _command_output(command: List[str]) -> str:
    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError:
        return "not_found"
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"error({proc.returncode}): {output or 'unknown'}"
    return output or "ok"


def _git_sha() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    result = _command_output(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"])
    if result.startswith("error(") or result == "not_found":
        return "unknown"
    return result.splitlines()[0].strip()


def _debug_focus_terms() -> List[str]:
    raw = os.getenv("E055_DEBUG_FOCUS_TERMS", "").strip()
    if raw:
        terms = [compact_text(item).upper() for item in raw.split(",") if compact_text(item)]
        return terms
    return [compact_text(item).upper() for item in DEFAULT_DEBUG_FOCUS_TERMS]


def _row_matches_focus(row_text: str, focus_terms: List[str]) -> bool:
    if not focus_terms:
        return True
    compact = compact_text(row_text).upper()
    return any(term in compact for term in focus_terms)


def strip_times_marker_from_model(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    normalized = re.sub(r"\s*([,、/／|])\s*", r" \1 ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized.strip(" ,、/／|")


def split_equivalent_model(value: str) -> Tuple[str, str]:
    text = normalize_text(value).strip()
    text = text.replace("：", ":")
    if ":" in text:
        maker, model = text.split(":", 1)
        return maker.strip(), strip_times_marker_from_model(model)
    return "", strip_times_marker_from_model(text)


def write_csv(rows: List[Dict[str, str]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            normalized_row = {}
            for column in OUTPUT_COLUMNS:
                value = ""
                for source_key in OUTPUT_COLUMN_SOURCE_KEYS[column]:
                    if source_key in row:
                        value = str(row.get(source_key, "") or "")
                        break
                normalized_row[column] = value
            writer.writerow(normalized_row)


def _extract_words(client: vision.ImageAnnotatorClient, page_image: Image.Image) -> List[WordBox]:
    buf = io.BytesIO()
    page_image.save(buf, format="PNG")
    image = vision.Image(content=buf.getvalue())
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Vision API error: {response.error.message}")

    annotation = response.full_text_annotation
    if not annotation.pages:
        return []

    words: List[WordBox] = []
    for page in annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols).strip()
                    if not text:
                        continue
                    vertices = word.bounding_box.vertices
                    xs = [v.x if v.x is not None else 0 for v in vertices]
                    ys = [v.y if v.y is not None else 0 for v in vertices]
                    x0, x1 = float(min(xs)), float(max(xs))
                    y0, y1 = float(min(ys)), float(max(ys))
                    words.append(
                        WordBox(
                            text=text,
                            cx=(x0 + x1) / 2.0,
                            cy=(y0 + y1) / 2.0,
                            bbox=(x0, y0, x1, y1),
                        )
                    )
    return words


def _cluster_by_y(words: List[WordBox], threshold: float) -> List[RowCluster]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w.cy)
    clusters: List[RowCluster] = [RowCluster(row_y=sorted_words[0].cy, words=[sorted_words[0]])]
    for word in sorted_words[1:]:
        last = clusters[-1]
        if abs(word.cy - last.row_y) <= threshold:
            last.words.append(word)
            size = len(last.words)
            last.row_y = ((last.row_y * (size - 1)) + word.cy) / size
        else:
            clusters.append(RowCluster(row_y=word.cy, words=[word]))
    return clusters


def _row_text(cluster: RowCluster) -> str:
    return " ".join(normalize_text(w.text).strip() for w in sorted(cluster.words, key=lambda x: x.cx)).strip()


def _split_cluster_by_x_gap(cluster: RowCluster, max_gap: float = 44.0) -> List[RowCluster]:
    words = sorted(cluster.words, key=lambda w: w.cx)
    if not words:
        return []

    groups: List[List[WordBox]] = [[words[0]]]
    prev = words[0]
    for word in words[1:]:
        gap = word.bbox[0] - prev.bbox[2]
        if gap > max_gap:
            groups.append([word])
        else:
            groups[-1].append(word)
        prev = word

    split_rows = []
    for group in groups:
        split_rows.append(
            RowCluster(
                row_y=sum(w.cy for w in group) / len(group),
                words=group,
            )
        )
    return split_rows


def _collect_column_text(words: List[WordBox]) -> str:
    return " ".join(normalize_text(w.text).strip() for w in sorted(words, key=lambda x: x.cx)).strip()


def _is_header_row(value: str) -> bool:
    text = compact_text(value)
    return "相当型番" in text and "器具記" in text


def _normalize_code_token(value: str) -> str:
    normalized = normalize_text(value)
    normalized = normalized.replace("’", "'").replace("`", "'")
    normalized = normalized.strip("[](){}<>|,.;")
    return normalized


def _is_equipment_code_token(value: str) -> bool:
    token = _normalize_code_token(value)
    if not token:
        return False
    upper = token.upper()
    if upper in EXCLUDED_EMERGENCY_CODES:
        return True
    allowed_prefixes = ("CD", "CR", "CT", "UK", "WL", "CL", "XC", "X'C", "YC", "Y'C", "DL", "LL", "L", "TP", "GL", "SP", "ES", "EC")
    for prefix in allowed_prefixes:
        if not upper.startswith(prefix):
            continue
        suffix = upper[len(prefix):]
        if not suffix:
            return False
        if re.fullmatch(r"\d{1,2}[A-Z]?", suffix):
            return True
        if re.fullmatch(r"\d{1,2}G", suffix):
            return True
    return False


def _cleanup_model_text(value: str) -> str:
    text = normalize_text(value)
    text = DASH_VARIANTS_PATTERN.sub("-", text)
    text = re.split(r"\s+\d+\.(?=\s)", text, maxsplit=1)[0]
    text = text.split("。", 1)[0]
    text = text.strip(" |[]")
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _append_multiplier_suffix(text: str, model: str, model_end: int) -> str:
    suffix_match = MODEL_MULTIPLIER_SUFFIX_PATTERN.match(text[model_end:])
    suffix = suffix_match.group(0) if suffix_match else ""
    return _cleanup_model_text(f"{model}{suffix}")


def _normalize_for_model_matching(value: str) -> str:
    normalized = normalize_text(value).upper()
    return re.sub(r"[\s\-_ー―−–—‐ｰ]+", "", normalized)  # noqa: RUF001


def _is_emergency_certification_model(model: str) -> bool:
    normalized = _normalize_for_model_matching(model)
    if not normalized:
        return False
    return normalized.startswith("LALE") and bool(re.search(r"\d", normalized))


def _should_skip_output_row(equipment: str, model: str) -> bool:
    compact_equipment = compact_text(equipment).upper()
    if not model:
        return True
    if compact_equipment in EXCLUDED_EMERGENCY_CODES:
        return True
    return _is_emergency_certification_model(model)


def _char_pos_to_token_index(tokens: List[str], char_pos: int) -> int:
    cursor = 0
    for idx, token in enumerate(tokens):
        next_cursor = cursor + len(token)
        if cursor <= char_pos < next_cursor:
            return idx
        cursor = next_cursor + 1
    # Empty-token rows should safely map to 0; otherwise prefer the last token
    # to avoid surprising fallback-to-first behavior on mapping mismatch.
    return max(len(tokens) - 1, 0)


def _extract_maker_and_model(segment_text: str) -> Tuple[str, str, int]:
    matched = re.search(r"([A-Za-z][A-Za-z0-9&._-]{1,30})\s*[:：]\s*(.+)", segment_text)  # noqa: RUF001
    if not matched:
        return "", "", -1
    maker = matched.group(1).strip()
    model = _cleanup_model_text(matched.group(2))
    return maker, model, matched.start(1)


def _resolve_model_x(source: Dict[str, object], fallback: Dict[str, object] | None = None) -> float:
    fallback = fallback or {}
    value = source.get("model_x", source.get("row_x", fallback.get("model_x", fallback.get("row_x", 0.0))))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_model_without_colon_with_start(segment_text: str) -> Tuple[str, int]:
    text = _cleanup_model_text(segment_text)
    hyphen_model = MODEL_PATTERN.search(text)
    if hyphen_model:
        return _append_multiplier_suffix(text, hyphen_model.group(1), hyphen_model.end(1)), hyphen_model.start(1)
    return "", -1


def _extract_model_without_colon(segment_text: str) -> str:
    model, _ = _extract_model_without_colon_with_start(segment_text)
    return model


def _normalize_doujou_model(segment_text: str) -> str:
    compact = compact_text(segment_text).lower()
    if "同上" not in compact:
        return ""
    if re.search(r"(ガ[ー-]?ド|犬[-ー]?f|一卡付|卡付|カード|力[ー一-]?[f\u013e\u0142]?付)", compact):
        return "同上ガード付"
    return "同上"


def _cluster_x_positions(values: List[float], tolerance: float = 220.0) -> List[float]:
    if not values:
        return []
    sorted_values = sorted(values)
    clusters: List[List[float]] = [[sorted_values[0]]]
    for value in sorted_values[1:]:
        if abs(value - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _assign_block_indexes_with_centers(
    section_candidates: List[Dict[str, object]],
    *,
    x_centers: List[float],
) -> None:
    for row in section_candidates:
        if not x_centers:
            row["block_index"] = 0
            continue
        x = float(row.get("row_x", 0.0))
        row["block_index"] = min(range(len(x_centers)), key=lambda idx: abs(x - x_centers[idx]))


def _section_bounds_from_clusters(
    section_clusters: List[RowCluster],
    *,
    page_image: Image.Image,
) -> Dict[str, float]:
    width, height = page_image.size
    all_words: List[WordBox] = []
    for cluster in section_clusters:
        all_words.extend(cluster.words)
    if not all_words:
        return {
            "x_min": 0.0,
            "x_max": float(width),
            "y_min": 0.0,
            "y_max": float(height),
        }

    x_min = max(0.0, min(word.bbox[0] for word in all_words) - 20.0)
    x_max = min(float(width), max(word.bbox[2] for word in all_words) + 20.0)
    y_min = max(0.0, min(word.bbox[1] for word in all_words) - 14.0)
    y_max = min(float(height), max(word.bbox[3] for word in all_words) + 14.0)
    if x_max <= x_min:
        x_min, x_max = 0.0, float(width)
    if y_max <= y_min:
        y_min, y_max = 0.0, float(height)
    return {
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
    }


def _count_unresolved_equipment(section_candidates: List[Dict[str, object]]) -> int:
    unresolved = 0
    for row in section_candidates:
        has_model = bool(str(row.get("相当型番", "")).strip())
        has_equipment = bool(str(row.get("機器器具", "")).strip())
        if has_model and not has_equipment:
            unresolved += 1
    return unresolved


def _average_model_block_alignment_distance(section_candidates: List[Dict[str, object]]) -> float:
    by_block: Dict[int, List[float]] = {}
    for row in section_candidates:
        block_index = int(row.get("block_index", 0))
        model_x = _resolve_model_x(row)
        by_block.setdefault(block_index, []).append(model_x)
    if not by_block:
        return 0.0

    block_centers: Dict[int, float] = {}
    for block_index, xs in by_block.items():
        if not xs:
            continue
        block_centers[block_index] = sum(xs) / len(xs)

    distances: List[float] = []
    for row in section_candidates:
        block_index = int(row.get("block_index", 0))
        if block_index not in block_centers:
            continue
        model_x = _resolve_model_x(row)
        distances.append(abs(model_x - block_centers[block_index]))
    if not distances:
        return 0.0
    return sum(distances) / len(distances)


def _should_run_line_assist(
    section_candidates: List[Dict[str, object]],
    *,
    x_centers: List[float],
    section_bounds: Dict[str, float],
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    total = len(section_candidates)
    if total == 0:
        return False, reasons

    continuation_rows = [
        row for row in section_candidates
        if str(row.get("相当型番", "")).strip() and not str(row.get("機器器具", "")).strip()
    ]
    continuation_ratio = len(continuation_rows) / max(total, 1)
    if len(continuation_rows) >= 2 and continuation_ratio >= 0.35:
        reasons.append("high_continuation_ratio")

    sorted_centers = sorted(x_centers)
    if len(sorted_centers) >= 2:
        min_gap = min(abs(b - a) for a, b in zip(sorted_centers, sorted_centers[1:]))
        if min_gap < 130.0:
            reasons.append("dense_x_centers")

    cross_model = 0
    for row in section_candidates:
        row_x = float(row.get("row_x", 0.0))
        model_x = _resolve_model_x(row, row)
        if abs(model_x - row_x) > 420.0:
            cross_model += 1
    if cross_model >= 2:
        reasons.append("cross_model_x")

    section_width = max(float(section_bounds.get("x_max", 0.0)) - float(section_bounds.get("x_min", 0.0)), 1.0)
    if total <= 2 and section_width > 900.0:
        reasons.append("sparse_candidates_in_wide_section")

    return bool(reasons), reasons


def _collect_vector_vertical_lines(
    *,
    pdf_path: Path,
    page_number: int,
    section_bounds: Dict[str, float],
    page_image: Image.Image,
) -> Tuple[List[float], Dict[str, object]]:
    diagnostics: Dict[str, object] = {"source": "vector", "error": "", "raw_lines": 0}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[page_number - 1]
            width_px, height_px = page_image.size
            scale_x = float(width_px) / float(page.width)
            scale_y = float(height_px) / float(page.height)

            y_min = float(section_bounds.get("y_min", 0.0))
            y_max = float(section_bounds.get("y_max", float(height_px)))
            x_min = float(section_bounds.get("x_min", 0.0))
            x_max = float(section_bounds.get("x_max", float(width_px)))
            section_height = max(y_max - y_min, 1.0)

            x_positions: List[float] = []
            for line in page.lines:
                x0 = float(line.get("x0", 0.0))
                x1 = float(line.get("x1", 0.0))
                y0 = float(line.get("y0", 0.0))
                y1 = float(line.get("y1", 0.0))
                if abs(x0 - x1) > 1.0:
                    continue

                x_px = x0 * scale_x
                top_px = (float(page.height) - max(y0, y1)) * scale_y
                bottom_px = (float(page.height) - min(y0, y1)) * scale_y
                if bottom_px < y_min - 8.0 or top_px > y_max + 8.0:
                    continue
                if x_px < x_min - 40.0 or x_px > x_max + 40.0:
                    continue
                line_length = max(bottom_px - top_px, 0.0)
                if line_length < section_height * 0.35:
                    continue
                x_positions.append(round(x_px, 2))

            diagnostics["raw_lines"] = len(x_positions)
            return _cluster_x_positions(x_positions, tolerance=8.0), diagnostics
    except Exception as exc:  # pragma: no cover - runtime fallback path
        diagnostics["error"] = str(exc)
        return [], diagnostics


def _collect_image_vertical_lines(
    *,
    page_image: Image.Image,
    section_bounds: Dict[str, float],
    time_budget_ms: int,
    start_time: float,
) -> Tuple[List[float], Dict[str, object]]:
    diagnostics: Dict[str, object] = {
        "source": "image",
        "error": "",
        "elapsed_ms": 0.0,
        "timed_out": False,
        "raw_lines": 0,
    }
    if cv2 is None or np is None:
        diagnostics["error"] = "opencv_or_numpy_unavailable"
        return [], diagnostics

    now = perf_counter()
    elapsed_ms = (now - start_time) * 1000.0
    remaining_ms = max(float(time_budget_ms) - elapsed_ms, 0.0)
    if remaining_ms <= 1.0:
        diagnostics["timed_out"] = True
        diagnostics["elapsed_ms"] = elapsed_ms
        return [], diagnostics

    image_arr = np.array(page_image)  # type: ignore[arg-type]
    height, width = image_arr.shape[:2]
    x_min = int(max(float(section_bounds.get("x_min", 0.0)) - 32.0, 0.0))
    x_max = int(min(float(section_bounds.get("x_max", float(width))) + 32.0, float(width)))
    y_min = int(max(float(section_bounds.get("y_min", 0.0)) - 16.0, 0.0))
    y_max = int(min(float(section_bounds.get("y_max", float(height))) + 16.0, float(height)))
    if x_max <= x_min or y_max <= y_min:
        diagnostics["error"] = "invalid_roi"
        diagnostics["elapsed_ms"] = (perf_counter() - start_time) * 1000.0
        return [], diagnostics

    roi = image_arr[y_min:y_max, x_min:x_max]
    if roi.size == 0:
        diagnostics["error"] = "empty_roi"
        diagnostics["elapsed_ms"] = (perf_counter() - start_time) * 1000.0
        return [], diagnostics

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        9,
    )
    kernel_height = max(12, (y_max - y_min) // 18)
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_height))
    vertical_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    min_line_length = max(int((y_max - y_min) * 0.35), 20)
    lines = cv2.HoughLinesP(
        vertical_mask,
        rho=1,
        theta=np.pi / 180.0,
        threshold=30,
        minLineLength=min_line_length,
        maxLineGap=8,
    )
    x_positions: List[float] = []
    if lines is not None:
        for entry in lines:
            x0, y0, x1, y1 = [int(v) for v in entry[0]]
            if abs(x0 - x1) > 4:
                continue
            line_length = abs(y1 - y0)
            if line_length < min_line_length:
                continue
            x_positions.append(round(float((x0 + x1) / 2.0 + x_min), 2))

    clustered_x_positions = _cluster_x_positions(x_positions, tolerance=10.0)
    diagnostics["raw_lines"] = len(x_positions)
    diagnostics["elapsed_ms"] = (perf_counter() - start_time) * 1000.0
    if diagnostics["elapsed_ms"] > float(time_budget_ms):
        diagnostics["timed_out"] = True
    return clustered_x_positions, diagnostics


def _merge_vertical_lines(
    *,
    vector_lines: List[float],
    image_lines: List[float],
    tolerance: float = 18.0,
) -> List[float]:
    merged = sorted(vector_lines + image_lines)
    if not merged:
        return []
    clusters: List[List[float]] = [[merged[0]]]
    for value in merged[1:]:
        if abs(value - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _build_line_based_blocks(
    *,
    vertical_xs: List[float],
    section_x_range: Tuple[float, float],
) -> List[Tuple[float, float]]:
    x_min, x_max = section_x_range
    bounds = [x_min]
    bounds.extend(x for x in vertical_xs if x_min <= x <= x_max)
    bounds.append(x_max)
    bounds = sorted(bounds)
    compact_bounds: List[float] = []
    for value in bounds:
        if not compact_bounds or abs(value - compact_bounds[-1]) > 18.0:
            compact_bounds.append(value)
    blocks: List[Tuple[float, float]] = []
    for left, right in zip(compact_bounds, compact_bounds[1:]):
        if right - left < 70.0:
            continue
        blocks.append((left, right))
    return blocks


def _line_assist_confidence(
    *,
    section_candidates: List[Dict[str, object]],
    line_blocks: List[Tuple[float, float]],
    vector_line_count: int,
    image_line_count: int,
    baseline_center_count: int,
) -> float:
    if not section_candidates or not line_blocks:
        return 0.0

    coverage_hits = 0
    for row in section_candidates:
        x = float(row.get("row_x", 0.0))
        if any((left - 8.0) <= x <= (right + 8.0) for left, right in line_blocks):
            coverage_hits += 1
    coverage = coverage_hits / max(len(section_candidates), 1)

    line_strength = min((vector_line_count + image_line_count) / 8.0, 1.0)
    block_count_score = 1.0 if 1 <= len(line_blocks) <= 8 else 0.3
    baseline_alignment = 1.0 if abs(len(line_blocks) - max(baseline_center_count, 1)) <= 2 else 0.5
    confidence = (
        0.45 * coverage
        + 0.25 * line_strength
        + 0.20 * block_count_score
        + 0.10 * baseline_alignment
    )
    return max(0.0, min(confidence, 1.0))


def _apply_line_assist_if_confident(
    *,
    section_candidates: List[Dict[str, object]],
    section_bounds: Dict[str, float],
    baseline_x_centers: List[float],
    page_image: Image.Image,
    pdf_path: Path,
    page_number: int,
    config: LineAssistConfig,
) -> Dict[str, object]:
    info: Dict[str, object] = {
        "invoked": False,
        "adopted": False,
        "confidence": 0.0,
        "rejected_reason": "",
        "vector_line_count": 0,
        "image_line_count": 0,
        "line_block_count": 0,
        "elapsed_ms": 0.0,
        "vector_diagnostics": {},
        "image_diagnostics": {},
    }
    if not section_candidates:
        info["rejected_reason"] = "no_section_candidates"
        return info

    info["invoked"] = True
    assist_start = perf_counter()
    vector_lines, vector_diag = _collect_vector_vertical_lines(
        pdf_path=pdf_path,
        page_number=page_number,
        section_bounds=section_bounds,
        page_image=page_image,
    )
    image_lines, image_diag = _collect_image_vertical_lines(
        page_image=page_image,
        section_bounds=section_bounds,
        time_budget_ms=config.latency_budget_ms,
        start_time=assist_start,
    )
    merged_lines = _merge_vertical_lines(vector_lines=vector_lines, image_lines=image_lines)
    line_blocks = _build_line_based_blocks(
        vertical_xs=merged_lines,
        section_x_range=(float(section_bounds.get("x_min", 0.0)), float(section_bounds.get("x_max", 0.0))),
    )

    info["vector_line_count"] = len(vector_lines)
    info["image_line_count"] = len(image_lines)
    info["line_block_count"] = len(line_blocks)
    info["vector_diagnostics"] = vector_diag
    info["image_diagnostics"] = image_diag
    info["elapsed_ms"] = (perf_counter() - assist_start) * 1000.0
    confidence = _line_assist_confidence(
        section_candidates=section_candidates,
        line_blocks=line_blocks,
        vector_line_count=len(vector_lines),
        image_line_count=len(image_lines),
        baseline_center_count=len(baseline_x_centers),
    )
    info["confidence"] = confidence

    if confidence < config.min_confidence:
        info["rejected_reason"] = "confidence_below_threshold"
        return info
    if not line_blocks:
        info["rejected_reason"] = "no_line_blocks"
        return info

    block_centers = [(left + right) / 2.0 for left, right in line_blocks]
    baseline_rows = [dict(row) for row in section_candidates]
    assisted_rows = [dict(row) for row in section_candidates]

    _propagate_equipment_in_section(baseline_rows)
    baseline_unresolved = _count_unresolved_equipment(baseline_rows)
    baseline_alignment = _average_model_block_alignment_distance(baseline_rows)

    _assign_block_indexes_with_centers(assisted_rows, x_centers=block_centers)
    _propagate_equipment_in_section(assisted_rows)
    assisted_unresolved = _count_unresolved_equipment(assisted_rows)
    assisted_alignment = _average_model_block_alignment_distance(assisted_rows)

    unresolved_improved = assisted_unresolved < baseline_unresolved
    alignment_improved = assisted_alignment + 1.0 < baseline_alignment
    if not unresolved_improved and not alignment_improved:
        info["rejected_reason"] = "no_quality_gain"
        return info

    for index, row in enumerate(section_candidates):
        row["block_index"] = assisted_rows[index].get("block_index", row.get("block_index", 0))
    info["adopted"] = True
    return info


def _extract_model_only_candidates(words: List[WordBox]) -> List[Dict[str, object]]:
    sorted_words = sorted(words, key=lambda item: item.cx)
    if len(sorted_words) < 2:
        return []

    tokens = [normalize_text(word.text).strip() for word in sorted_words]
    row_text = " ".join(tokens)
    normalized_row_text = DASH_VARIANTS_PATTERN.sub("-", row_text)
    if not re.search(r"\d+(?:\.\d+)?\s*W", row_text, flags=re.IGNORECASE):
        return []
    candidates: List[Dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for match in MODEL_PATTERN.finditer(normalized_row_text):
        model = _append_multiplier_suffix(normalized_row_text, match.group(1), match.end(1))
        if not model:
            continue

        token_index = _char_pos_to_token_index(tokens, match.start())
        key = (token_index, model)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "row_x": round(float(sorted_words[token_index].bbox[0]), 2),
                "model_x": round(float(sorted_words[token_index].bbox[0]), 2),
                "機器器具": "",
                "相当型番": model,
            }
        )
    return candidates


def _extract_colon_model_only_candidates(words: List[WordBox]) -> List[Dict[str, object]]:
    sorted_words = sorted(words, key=lambda item: item.cx)
    if len(sorted_words) < 2:
        return []

    tokens = [normalize_text(word.text).strip() for word in sorted_words]
    row_text = " ".join(tokens)
    normalized_row_text = DASH_VARIANTS_PATTERN.sub("-", row_text)
    candidates: List[Dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    # Intentionally no wattage guard here: continuation rows may contain only
    # maker:model text (e.g. "DAIKO:LZA-93039") and still need extraction.
    for match in COLON_MODEL_PATTERN.finditer(normalized_row_text):
        maker = match.group(1).strip()
        model = _append_multiplier_suffix(normalized_row_text, match.group(2), match.end(2))
        if not maker or not model:
            continue

        equivalent_model = f"{maker}:{model}"
        token_index = _char_pos_to_token_index(tokens, match.start(1))
        key = (token_index, equivalent_model)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "row_x": round(float(sorted_words[token_index].bbox[0]), 2),
                "model_x": round(float(sorted_words[token_index].bbox[0]), 2),
                "機器器具": "",
                "相当型番": equivalent_model,
            }
        )
    return candidates


def _propagate_equipment_in_section(section_candidates: List[Dict[str, object]]) -> None:
    rows_by_y: Dict[float, List[Dict[str, object]]] = {}
    for row in section_candidates:
        y = float(row.get("row_y", 0.0))
        rows_by_y.setdefault(y, []).append(row)

    sorted_ys = sorted(rows_by_y.keys())
    for idx, y in enumerate(sorted_ys):
        current_rows = sorted(rows_by_y[y], key=lambda item: float(item.get("row_x", 0.0)))
        if any(str(row.get("機器器具", "")).strip() for row in current_rows):
            continue

        source_rows: List[Dict[str, object]] = []
        source_y = None
        for prev_y in reversed(sorted_ys[:idx]):
            prev_rows = sorted(rows_by_y[prev_y], key=lambda item: float(item.get("row_x", 0.0)))
            prev_rows = [row for row in prev_rows if str(row.get("機器器具", "")).strip()]
            if prev_rows:
                source_rows = prev_rows
                source_y = prev_y
                break
        if not source_rows or source_y is None:
            continue
        if abs(y - source_y) > 120.0:
            continue

        if len(current_rows) == len(source_rows):
            for row_index, row in enumerate(current_rows):
                source = source_rows[row_index]
                row["機器器具"] = source.get("機器器具", "")
                row["block_index"] = source.get("block_index", row.get("block_index", 0))
                row["model_x"] = _resolve_model_x(source, row)
        else:
            available_sources = list(source_rows)
            for row in current_rows:
                row_model_x = _resolve_model_x(row)
                source_pool = available_sources or source_rows
                source = min(
                    source_pool,
                    key=lambda source_row: abs(
                        _resolve_model_x(source_row) - row_model_x
                    ),
                )
                row["機器器具"] = source.get("機器器具", "")
                row["block_index"] = source.get("block_index", row.get("block_index", 0))
                row["model_x"] = _resolve_model_x(source, row)
                if source in available_sources:
                    available_sources.remove(source)

    by_block: Dict[int, List[Dict[str, object]]] = {}
    for row in section_candidates:
        block_index = int(row.get("block_index", 0))
        by_block.setdefault(block_index, []).append(row)

    for rows in by_block.values():
        rows.sort(key=lambda item: (float(item.get("row_y", 0.0)), float(item.get("row_x", 0.0))))
        last_equipment = ""
        for row in rows:
            equipment = str(row.get("機器器具", "")).strip()
            if equipment:
                last_equipment = equipment
                continue
            if last_equipment:
                row["機器器具"] = last_equipment


def _extract_candidates_from_cluster(cluster: RowCluster) -> List[Dict[str, object]]:
    words = sorted(cluster.words, key=lambda item: item.cx)
    if not words:
        return []
    tokens = [normalize_text(word.text).strip() for word in words]
    code_indexes = [idx for idx, token in enumerate(tokens) if _is_equipment_code_token(token)]
    if not code_indexes:
        has_colon_token = any(":" in token or "\uFF1A" in token for token in tokens)
        if has_colon_token:
            colon_candidates = _extract_colon_model_only_candidates(words)
            if colon_candidates:
                return colon_candidates
        model_only_candidates = _extract_model_only_candidates(words)
        if model_only_candidates:
            return model_only_candidates
        return []

    candidates: List[Dict[str, object]] = []
    for index, code_start in enumerate(code_indexes):
        code_end = code_indexes[index + 1] if index + 1 < len(code_indexes) else len(tokens)
        segment_tokens = tokens[code_start:code_end]
        segment_text = " ".join(segment_tokens).strip()
        if not segment_text:
            continue

        equipment = _normalize_code_token(segment_tokens[0])
        equivalent_model = ""
        row_x = round(float(words[code_start].bbox[0]), 2)
        model_x = row_x
        if ":" in segment_text or "：" in segment_text:  # noqa: RUF001
            maker, model, maker_start = _extract_maker_and_model(segment_text)
            if maker and model:
                equivalent_model = f"{maker}:{model}"
                maker_token_index = _char_pos_to_token_index(segment_tokens, maker_start)
                model_x = round(float(words[code_start + maker_token_index].bbox[0]), 2)
            elif model:
                equivalent_model = model
        else:
            remainder = " ".join(segment_tokens[1:])
            equivalent_model = _normalize_doujou_model(remainder)
            if not equivalent_model:
                equivalent_model, model_start = _extract_model_without_colon_with_start(remainder)
                if model_start >= 0:
                    model_token_index = 1 + _char_pos_to_token_index(segment_tokens[1:], model_start)
                    model_x = round(float(words[code_start + model_token_index].bbox[0]), 2)

        if not equivalent_model:
            continue

        candidates.append(
            {
                "row_x": row_x,
                "model_x": model_x,
                "機器器具": equipment,
                "相当型番": equivalent_model,
            }
        )
    return candidates


def build_output_rows(candidates: List[Dict[str, object]]) -> List[Dict[str, str]]:
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            int(item.get("page", 0)),
            int(item.get("section_index", 0)),
            int(item.get("block_index", 0)),
            float(item.get("row_y", 0.0)),
            float(item.get("row_x", 0.0)),
        ),
    )
    rows: List[Dict[str, str]] = []
    for item in sorted_candidates:
        equivalent_model = str(item.get("相当型番", "")).strip()
        manufacturer, model = split_equivalent_model(equivalent_model)
        equipment = str(item.get("機器器具", "")).strip()
        if _should_skip_output_row(equipment, model):
            continue
        rows.append(
            {
                "機器器具": equipment,
                "メーカー": manufacturer,
                "型番": model,
            }
        )
    return rows


def _collect_focus_row_samples(
    clusters: List[RowCluster],
    *,
    focus_terms: List[str],
    row_limit: int,
) -> List[Dict[str, object]]:
    samples: List[Dict[str, object]] = []
    for cluster in clusters:
        row_text = _row_text(cluster)
        if not _row_matches_focus(row_text, focus_terms):
            continue
        tokens = sorted(cluster.words, key=lambda x: x.cx)
        samples.append(
            {
                "row_y": round(cluster.row_y, 2),
                "row_text": row_text,
                "tokens": [
                    {
                        "text": normalize_text(token.text),
                        "bbox": [round(float(v), 2) for v in token.bbox],
                    }
                    for token in tokens
                ],
            }
        )
        if len(samples) >= max(row_limit, 1):
            break
    return samples


def _print_diagnostics_summary(diagnostics: Dict[str, object]) -> None:
    page_diagnostics = diagnostics.get("page_diagnostics", [])
    if not isinstance(page_diagnostics, list):
        return

    row_print_limit = _int_env("E055_DEBUG_LOG_ROW_LIMIT", 20)
    candidate_print_limit = _int_env("E055_DEBUG_LOG_CANDIDATE_LIMIT", 20)
    for page_diag in page_diagnostics:
        if not isinstance(page_diag, dict):
            continue
        page = page_diag.get("page")
        focus_rows = page_diag.get("focus_rows", [])
        candidate_rows = page_diag.get("candidate_rows", [])
        print(
            f"[E055 DEBUG] page={page} focus_rows={len(focus_rows) if isinstance(focus_rows, list) else 0} "
            f"candidate_rows={len(candidate_rows) if isinstance(candidate_rows, list) else 0}"
        )
        if isinstance(focus_rows, list):
            for row in focus_rows[:max(row_print_limit, 0)]:
                if not isinstance(row, dict):
                    continue
                print(f"[E055 DEBUG][focus] page={page} y={row.get('row_y')} text={row.get('row_text', '')}")
        if isinstance(candidate_rows, list):
            for row in candidate_rows[:max(candidate_print_limit, 0)]:
                if not isinstance(row, dict):
                    continue
                print(
                    "[E055 DEBUG][candidate] "
                    f"page={page} y={row.get('row_y')} block={row.get('block_index')} "
                    f"eq={row.get('機器器具', '')} model={row.get('相当型番', '')} "
                    f"row_x={row.get('row_x')} model_x={row.get('model_x')}"
                )


def _extract_page_candidate_rows(
    *,
    pdf_path: Path,
    client: vision.ImageAnnotatorClient,
    page_image: Image.Image,
    page_number: int,
    y_cluster: float,
    diagnostics: Dict[str, object] | None = None,
    line_assist_config: LineAssistConfig | None = None,
) -> List[Dict[str, object]]:
    line_assist_config = line_assist_config or _line_assist_config()
    words = _extract_words(client, page_image)
    clusters = _cluster_by_y(words, y_cluster)
    header_indexes = [idx for idx, cluster in enumerate(clusters) if _is_header_row(_row_text(cluster))]
    page_diag_candidates: List[Dict[str, object]] = []
    page_diag_line_assist: List[Dict[str, object]] = []
    if not header_indexes:
        if diagnostics is not None:
            page_diagnostics = diagnostics.setdefault("page_diagnostics", [])
            if not isinstance(page_diagnostics, list):
                page_diagnostics = []
                diagnostics["page_diagnostics"] = page_diagnostics
            page_diagnostics.append(
                {
                    "page": page_number,
                    "word_count": len(words),
                    "cluster_count": len(clusters),
                    "header_indexes": [],
                    "focus_rows": _collect_focus_row_samples(
                        clusters,
                        focus_terms=list(diagnostics.get("focus_terms", [])),
                        row_limit=int(diagnostics.get("focus_row_limit", 30)),
                    ),
                    "candidate_rows": [],
                }
            )
        return []

    candidates: List[Dict[str, object]] = []
    for header_pos, header_index in enumerate(header_indexes):
        next_header_index = header_indexes[header_pos + 1] if header_pos + 1 < len(header_indexes) else len(clusters)
        section_clusters = clusters[header_index + 1:next_header_index]
        section_candidates: List[Dict[str, object]] = []
        for cluster in section_clusters:
            row_candidates = _extract_candidates_from_cluster(cluster)
            for row in row_candidates:
                section_candidates.append(
                    {
                        "page": page_number,
                        "section_index": header_pos,
                        "row_y": round(cluster.row_y, 2),
                        **row,
                    }
                )
        x_values = [
            float(row["row_x"])
            for row in section_candidates
            if str(row.get("機器器具", "")).strip()
        ]
        if not x_values:
            x_values = [float(row["row_x"]) for row in section_candidates]
        x_centers = _cluster_x_positions(x_values, tolerance=220.0)
        _assign_block_indexes_with_centers(section_candidates, x_centers=x_centers)

        section_bounds = _section_bounds_from_clusters(section_clusters, page_image=page_image)
        should_run_assist = False
        assist_reasons: List[str] = []
        if line_assist_config.mode == "force":
            should_run_assist = True
            assist_reasons = ["forced"]
        elif line_assist_config.mode == "auto":
            should_run_assist, assist_reasons = _should_run_line_assist(
                section_candidates,
                x_centers=x_centers,
                section_bounds=section_bounds,
            )

        assist_info: Dict[str, object] = {
            "section_index": header_pos,
            "mode": line_assist_config.mode,
            "triggered": should_run_assist,
            "trigger_reasons": assist_reasons,
            "adopted": False,
            "confidence": 0.0,
            "elapsed_ms": 0.0,
            "rejected_reason": "mode_off_or_not_triggered",
        }
        if should_run_assist:
            applied = _apply_line_assist_if_confident(
                section_candidates=section_candidates,
                section_bounds=section_bounds,
                baseline_x_centers=x_centers,
                page_image=page_image,
                pdf_path=pdf_path,
                page_number=page_number,
                config=line_assist_config,
            )
            assist_info.update(applied)
        if line_assist_config.debug_enabled or diagnostics is not None:
            page_diag_line_assist.append(assist_info)

        _propagate_equipment_in_section(section_candidates)
        for row in section_candidates:
            candidates.append(row)
            if diagnostics is not None and len(page_diag_candidates) < int(diagnostics.get("candidate_limit", 120)):
                page_diag_candidates.append(
                    {
                        "section_index": int(row.get("section_index", 0)),
                        "row_y": float(row.get("row_y", 0.0)),
                        "row_x": float(row.get("row_x", 0.0)),
                        "model_x": float(row.get("model_x", 0.0)),
                        "block_index": int(row.get("block_index", 0)),
                        "機器器具": str(row.get("機器器具", "")),
                        "相当型番": str(row.get("相当型番", "")),
                    }
                )
    if diagnostics is not None:
        page_diagnostics = diagnostics.setdefault("page_diagnostics", [])
        if not isinstance(page_diagnostics, list):
            page_diagnostics = []
            diagnostics["page_diagnostics"] = page_diagnostics
        page_diagnostics.append(
            {
                "page": page_number,
                "word_count": len(words),
                "cluster_count": len(clusters),
                "header_indexes": header_indexes,
                "focus_rows": _collect_focus_row_samples(
                    clusters,
                    focus_terms=list(diagnostics.get("focus_terms", [])),
                    row_limit=int(diagnostics.get("focus_row_limit", 30)),
                ),
                "candidate_rows": page_diag_candidates,
                "line_assist": page_diag_line_assist,
            }
        )
    return candidates


def extract_e055_pdf(
    pdf_path: Path,
    out_csv: Path,
    debug_dir: Path,
    vision_service_account_json: str,
    page: int = 0,
    dpi: int = 300,
    y_cluster: float = 18.0,
) -> Dict[str, object]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"入力PDFが見つかりません: {pdf_path}")

    line_assist_config = _line_assist_config()
    diagnostics_enabled = _is_truthy_env("E055_DEBUG_DIAGNOSTICS")
    diagnostics: Dict[str, object] | None = None
    diagnostics_path: Path | None = None
    if diagnostics_enabled:
        debug_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_path = debug_dir / "e055_diagnostics.json"
        diagnostics = {
            "enabled": True,
            "extractor_file": str(Path(__file__).resolve()),
            "git_sha": _git_sha(),
            "python_version": sys.version.split()[0],
            "pillow_version": _package_version("Pillow"),
            "vision_version": _package_version("google-cloud-vision"),
            "pdftoppm_version": _command_output(["pdftoppm", "-v"]),
            "dpi": dpi,
            "y_cluster": y_cluster,
            "focus_terms": _debug_focus_terms(),
            "focus_row_limit": _int_env("E055_DEBUG_FOCUS_ROW_LIMIT", 40),
            "candidate_limit": _int_env("E055_DEBUG_CANDIDATE_LIMIT", 200),
            "line_assist": {
                "mode": line_assist_config.mode,
                "latency_budget_ms": line_assist_config.latency_budget_ms,
                "min_confidence": line_assist_config.min_confidence,
                "debug_enabled": line_assist_config.debug_enabled,
            },
            "page_diagnostics": [],
        }
        print(
            "[E055 DEBUG] enabled "
            f"sha={diagnostics['git_sha']} "
            f"pdftoppm={diagnostics['pdftoppm_version'].splitlines()[0] if diagnostics['pdftoppm_version'] else 'unknown'}"
        )

    client = build_vision_client(vision_service_account_json)
    total_pages = count_pdf_pages(pdf_path)
    target_pages = resolve_target_pages(total_pages=total_pages, page=page)

    candidate_rows: List[Dict[str, object]] = []
    rows_by_page: Dict[int, int] = {}
    with TemporaryDirectory() as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        for target_page in target_pages:
            png_path = run_pdftoppm(pdf_path, target_page, dpi, tmp_dir)
            with Image.open(png_path) as source_image:
                page_image = source_image.convert("RGB")
            try:
                page_candidates = _extract_page_candidate_rows(
                    pdf_path=pdf_path,
                    client=client,
                    page_image=page_image,
                    page_number=target_page,
                    y_cluster=y_cluster,
                    diagnostics=diagnostics,
                    line_assist_config=line_assist_config,
                )
            finally:
                page_image.close()
            rows_by_page[target_page] = len(page_candidates)
            candidate_rows.extend(page_candidates)

    rows = build_output_rows(candidate_rows)
    write_csv(rows, out_csv)
    result = {
        "rows": len(rows),
        "columns": OUTPUT_COLUMNS,
        "output_csv": str(out_csv),
        "pages_processed": len(target_pages),
        "target_pages": target_pages,
        "rows_by_page": rows_by_page,
    }
    if diagnostics is not None and diagnostics_path is not None:
        diagnostics["input_pdf"] = str(pdf_path)
        diagnostics["input_pdf_size_bytes"] = pdf_path.stat().st_size
        diagnostics["target_pages"] = target_pages
        diagnostics["candidate_rows_total"] = len(candidate_rows)
        diagnostics["output_rows_total"] = len(rows)
        if _is_truthy_env("E055_DEBUG_LOG_SUMMARY", "1"):
            _print_diagnostics_summary(diagnostics)
        diagnostics_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[E055 DEBUG] wrote diagnostics to {diagnostics_path}")
        result["diagnostics_file"] = str(diagnostics_path)
        result["diagnostics_git_sha"] = str(diagnostics.get("git_sha", "unknown"))
        result["diagnostics_pdftoppm_version"] = str(diagnostics.get("pdftoppm_version", "unknown"))
    return result
