#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import subprocess
import sys
import tempfile
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Set, Tuple

try:
    from google.cloud import vision
    from google.oauth2 import service_account
    _VISION_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on local env
    vision = None
    service_account = None
    _VISION_IMPORT_ERROR = exc
from PIL import Image, ImageDraw
import pdfplumber


CORE_COLUMNS = ["機器番号", "機器名称", "電圧(V)", "容量(kW)"]
DRAWING_NUMBER_COLUMN = "図面番号"
OUTPUT_COLUMNS = CORE_COLUMNS + [DRAWING_NUMBER_COLUMN]
RESAMPLE_LANCZOS = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

SIDE_SPLITS = {
    "L": (0.0, 0.0, 0.5, 1.0),
    "R": (0.5, 0.0, 1.0, 1.0),
}

DEFAULT_CENTER_RATIOS = [0.24, 0.35, 0.40, 0.44]
HEADER_Y_CLUSTER = 22.0
DATA_START_OFFSET = 140.0
TABLE_HEADER_MIN_CATEGORIES = 3
TABLE_HEADER_X_MARGIN = 60.0
TABLE_HEADER_RIGHT_MARGIN = 360.0
TABLE_HEADER_TOP_MARGIN = 24.0
TABLE_MAX_SCAN_HEIGHT = 360.0
TABLE_SCAN_BOTTOM_TOLERANCE = 24.0
TABLE_MIN_WIDTH = 140.0
TABLE_MIN_HEIGHT = 45.0
TABLE_MERGE_IOU = 0.55
TABLE_NEARBY_HEADER_Y = 14.0
TABLE_NEARBY_HEADER_X = 45.0
TABLE_MIN_START_OFFSET = 10.0
TABLE_MAX_START_OFFSET = 36.0
TABLE_DEFAULT_START_OFFSET = 24.0
TABLE_TRAILING_NON_DATA_GAP = 1
TABLE_BOTTOM_NEAR_EDGE_PX = 28.0
TABLE_BOTTOM_EXPAND_STEP_PX = 36.0
TABLE_BOTTOM_EXPAND_MAX_TRIES = 6
TABLE_BOTTOM_EXPAND_MAX_RATIO = 0.45
TABLE_BOTTOM_EXPAND_NO_GROWTH_STREAK = 2
LEGACY_TRAILING_NON_DATA_GAP = 2
TABLE_HEADER_CLUSTER_X_GAP = 180.0
LEGACY_FIRST_PAGES = {1, 2}
DRAWING_NO_Y_CLUSTER = 22.0
DRAWING_NO_LABEL_TO_VALUE_MAX_OFFSET = 180.0
DRAWING_NO_LABEL_X_TOLERANCE_LEFT = 120.0
DRAWING_NO_LABEL_X_TOLERANCE_RIGHT = 320.0
DRAWING_NO_VALUE_Y_CLUSTER = 12.0
DRAWING_NO_BOTTOM_REGION_Y_RATIO = 0.70
DRAWING_NO_BOTTOM_REGION_X_RATIO = 0.70
DRAWING_NO_PATTERN = re.compile(
    r"^[A-Z]{1,4}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*$"
)

ROW_FILTER_NAME_KEYWORDS = [
    "ポンプ",
    "排風",
    "送風",
    "送気",
    "排気",
    "装置",
    "電源",
    "フロート",
    "シャッター",
    "弁",
    "ファン",
    "雨水",
    "排水",
    "清水",
    "汚泥",
]

HEADER_KEYWORDS = [
    "機器",
    "記号",
    "名称",
    "電圧",
    "容量",
    "備考",
    "起動",
    "回路",
    "whm",
    "インター",
]

FOOTER_KEYWORDS = [
    "図面",
    "縮尺",
    "建築",
    "設計",
    "コード",
    "三菱",
    "主管",
    "日付",
    "登録",
]


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


@dataclass
class ColumnBounds:
    x_min: float
    b12: float
    b23: float
    b34: float
    x_max: float
    header_y: float


@dataclass
class HeaderAnchor:
    row_y: float
    bbox: Tuple[float, float, float, float]
    categories: Tuple[str, ...]
    text: str


@dataclass
class TableCandidate:
    bbox: Tuple[float, float, float, float]
    header_y: float
    header_text: str
    categories: Tuple[str, ...]


@dataclass
class TableParseResult:
    table_index: int
    candidate: TableCandidate
    rows: List[Dict[str, object]]
    expand_attempts: int = 0
    final_crop_bottom: float = 0.0


@dataclass
class RowsFromWordsResult:
    rows: List[Dict[str, object]]
    saw_data: bool
    last_data_cluster_bottom: Optional[float]
    trailing_non_data_count: int
    stopped_by_footer: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google Cloud Vision APIでPDF表と図面番号を抽出する"
    )
    parser.add_argument("--pdf", default="/data/電気図1.pdf", help="入力PDFパス")
    parser.add_argument("--page", type=int, default=1, help="1始まりのページ番号（0以下で全ページ）")
    parser.add_argument("--dpi", type=int, default=300, help="pdftoppmのDPI")
    parser.add_argument("--out", default="/data/vision_output.csv", help="出力CSVパス")
    parser.add_argument("--debug-dir", default="/debug", help="デバッグ画像保存先")
    parser.add_argument(
        "--y-cluster",
        type=float,
        default=20.0,
        help="行グループ化に使うy距離しきい値(px)",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def normalize_drawing_number_candidate(text: str) -> Optional[str]:
    normalized = normalize_text(text).upper()
    normalized = normalized.replace(" ", "").replace("　", "")
    normalized = re.sub(r"[‐‑‒–—―ー−－]", "-", normalized)
    normalized = normalized.strip("|,:;[](){}<>「」『』")
    if DRAWING_NO_PATTERN.fullmatch(normalized):
        return normalized
    return None


def is_drawing_number_label(text: str) -> bool:
    normalized = normalize_text(text).replace(" ", "").replace("　", "")
    return "図面番号" in normalized or ("図面" in normalized and "番号" in normalized)


def extract_drawing_number_from_word_boxes(
    words: List[WordBox], frame_width: int, frame_height: int
) -> str:
    if not words:
        return ""

    clusters = cluster_by_y(words, DRAWING_NO_Y_CLUSTER)
    label_cluster: Optional[RowCluster] = None
    for cluster in clusters:
        if is_drawing_number_label(row_text(cluster)):
            if label_cluster is None or cluster.row_y > label_cluster.row_y:
                label_cluster = cluster

    if label_cluster is not None:
        label_words = sorted(label_cluster.words, key=lambda w: w.cx)
        label_y = label_cluster.row_y
        label_x_min = min(w.bbox[0] for w in label_words)
        label_x_max = max(w.bbox[2] for w in label_words)
        below_words = []
        for w in words:
            if w.cy <= label_y + 1.0:
                continue
            if w.cy > label_y + DRAWING_NO_LABEL_TO_VALUE_MAX_OFFSET:
                continue
            if w.bbox[2] < label_x_min - DRAWING_NO_LABEL_X_TOLERANCE_LEFT:
                continue
            if w.bbox[0] > label_x_max + DRAWING_NO_LABEL_X_TOLERANCE_RIGHT:
                continue
            below_words.append(w)

        for cluster in sorted(
            cluster_by_y(below_words, DRAWING_NO_VALUE_Y_CLUSTER), key=lambda c: c.row_y
        ):
            joined = "".join(w.text for w in sorted(cluster.words, key=lambda x: x.cx))
            candidate = normalize_drawing_number_candidate(joined)
            if candidate:
                return candidate
            for w in sorted(cluster.words, key=lambda x: x.cx):
                candidate = normalize_drawing_number_candidate(w.text)
                if candidate:
                    return candidate

    for w in sorted(words, key=lambda word: (word.cy, word.cx)):
        if w.cy < frame_height * DRAWING_NO_BOTTOM_REGION_Y_RATIO:
            continue
        if w.cx < frame_width * DRAWING_NO_BOTTOM_REGION_X_RATIO:
            continue
        candidate = normalize_drawing_number_candidate(w.text)
        if candidate:
            return candidate

    return ""


def extract_drawing_number_from_text_layer(pdf_path: Path, page: int) -> str:
    if page < 1:
        return ""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_index = page - 1
            if page_index >= len(pdf.pages):
                return ""
            target_page = pdf.pages[page_index]
            words_raw = target_page.extract_words(
                use_text_flow=True,
                keep_blank_chars=False,
            )
            if not words_raw:
                return ""
            words: List[WordBox] = []
            for item in words_raw:
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                x0 = float(item.get("x0", 0.0))
                x1 = float(item.get("x1", 0.0))
                top = float(item.get("top", 0.0))
                bottom = float(item.get("bottom", 0.0))
                words.append(
                    WordBox(
                        text=text,
                        cx=(x0 + x1) / 2.0,
                        cy=(top + bottom) / 2.0,
                        bbox=(x0, top, x1, bottom),
                    )
                )
            return extract_drawing_number_from_word_boxes(
                words,
                frame_width=int(target_page.width),
                frame_height=int(target_page.height),
            )
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "text-layer drawing number extraction failed: %s", exc
        )
        return ""


def resolve_drawing_number(
    *,
    pdf_path: Path,
    page: int,
    right_side_words: List[WordBox],
    right_side_size: Tuple[int, int],
) -> Tuple[str, str]:
    drawing_number = extract_drawing_number_from_word_boxes(
        right_side_words,
        frame_width=right_side_size[0],
        frame_height=right_side_size[1],
    )
    if drawing_number:
        return drawing_number, "vision"

    drawing_number = extract_drawing_number_from_text_layer(pdf_path=pdf_path, page=page)
    if drawing_number:
        return drawing_number, "text_layer"
    return "", "none"


def run_pdftoppm(pdf_path: Path, page: int, dpi: int, work_dir: Path) -> Path:
    if page < 1:
        raise ValueError("--page は1以上を指定してください。")
    png_base = work_dir / f"page_{page}"
    cmd = [
        "pdftoppm",
        "-f",
        str(page),
        "-l",
        str(page),
        "-singlefile",
        "-r",
        str(dpi),
        "-png",
        str(pdf_path),
        str(png_base),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "pdftoppm failed: "
            + (result.stderr.strip() if result.stderr else "unknown error")
        )
    png_path = png_base.with_suffix(".png")
    if not png_path.exists():
        raise FileNotFoundError(f"PNGが生成されませんでした: {png_path}")
    return png_path


def count_pdf_pages(pdf_path: Path) -> int:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return len(pdf.pages)


def resolve_target_pages(total_pages: int, page: int) -> List[int]:
    if total_pages < 1:
        raise ValueError("PDFにページがありません。")
    if page <= 0:
        return list(range(1, total_pages + 1))
    if page > total_pages:
        raise ValueError(f"指定ページがPDF範囲外です: page={page}, total_pages={total_pages}")
    return [page]


def split_sides(image: Image.Image) -> Dict[str, Image.Image]:
    width, height = image.size
    result: Dict[str, Image.Image] = {}
    for side, (x0r, y0r, x1r, y1r) in SIDE_SPLITS.items():
        x0 = int(width * x0r)
        y0 = int(height * y0r)
        x1 = int(width * x1r)
        y1 = int(height * y1r)
        result[side] = image.crop((x0, y0, x1, y1))
    return result


def extract_words(
    client: vision.ImageAnnotatorClient,
    side_image: Image.Image,
) -> List[WordBox]:
    buf = io.BytesIO()
    side_image.save(buf, format="PNG")
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
                    min_x, max_x = float(min(xs)), float(max(xs))
                    min_y, max_y = float(min(ys)), float(max(ys))
                    words.append(
                        WordBox(
                            text=text,
                            cx=(min_x + max_x) / 2.0,
                            cy=(min_y + max_y) / 2.0,
                            bbox=(min_x, min_y, max_x, max_y),
                        )
                    )
    return words


def cluster_by_y(words: List[WordBox], threshold: float) -> List[RowCluster]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w.cy)
    clusters: List[RowCluster] = [RowCluster(row_y=sorted_words[0].cy, words=[sorted_words[0]])]
    for word in sorted_words[1:]:
        last = clusters[-1]
        if abs(word.cy - last.row_y) <= threshold:
            last.words.append(word)
            n = len(last.words)
            last.row_y = ((last.row_y * (n - 1)) + word.cy) / n
        else:
            clusters.append(RowCluster(row_y=word.cy, words=[word]))
    return clusters


def row_text(cluster: RowCluster) -> str:
    return "".join(w.text for w in sorted(cluster.words, key=lambda x: x.cx))


def header_score(cluster: RowCluster) -> int:
    text = normalize_text(row_text(cluster)).lower()
    score = 0
    if "機器" in text:
        score += 1
    if "記号" in text:
        score += 1
    if "名称" in text:
        score += 1
    if "電圧" in text or "(v" in text or "v)" in text:
        score += 1
    if "kw" in text or "容量" in text:
        score += 1
    return score


def _header_categories_from_text(text: str) -> Set[str]:
    normalized = normalize_text(text).lower().replace(" ", "").replace("　", "")
    categories: Set[str] = set()

    if "機器番号" in normalized:
        categories.add("code")
    if "機器" in normalized and ("番号" in normalized or "記号" in normalized):
        categories.add("code")
    if ("機" in normalized and "器" in normalized and "番" in normalized and "号" in normalized):
        categories.add("code")

    if "名称" in normalized or ("名" in normalized and "称" in normalized):
        categories.add("name")

    if "電圧" in normalized or ("電" in normalized and "圧" in normalized):
        categories.add("voltage")
    if "(v" in normalized or "v)" in normalized:
        categories.add("voltage")

    if "容量" in normalized or ("容" in normalized and "量" in normalized):
        categories.add("power")
    if "kw" in normalized or "(kw" in normalized:
        categories.add("power")
    return categories


def _cluster_bbox(cluster: RowCluster) -> Tuple[float, float, float, float]:
    xs0 = [w.bbox[0] for w in cluster.words]
    ys0 = [w.bbox[1] for w in cluster.words]
    xs1 = [w.bbox[2] for w in cluster.words]
    ys1 = [w.bbox[3] for w in cluster.words]
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _bbox_intersection(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    h = max(0.0, min(ay1, by1) - max(ay0, by0))
    return w * h


def _bbox_area(b: Tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _bbox_iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    inter = _bbox_intersection(a, b)
    if inter <= 0:
        return 0.0
    union = _bbox_area(a) + _bbox_area(b) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _bbox_union(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _x_overlap_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    base = max(1.0, min(a[2] - a[0], b[2] - b[0]))
    return w / base


def detect_header_anchors(words: List[WordBox], y_cluster: float = HEADER_Y_CLUSTER) -> List[HeaderAnchor]:
    clusters = cluster_by_y(words, y_cluster)
    anchors: List[HeaderAnchor] = []
    for cluster in clusters:
        for segment in _split_cluster_by_x_gap(cluster, TABLE_HEADER_CLUSTER_X_GAP):
            text = row_text(segment)
            categories = _header_categories_from_text(text)
            if len(categories) < TABLE_HEADER_MIN_CATEGORIES:
                continue
            anchors.append(
                HeaderAnchor(
                    row_y=segment.row_y,
                    bbox=_cluster_bbox(segment),
                    categories=tuple(sorted(categories)),
                    text=text,
                )
            )

    anchors.sort(key=lambda a: (a.row_y, a.bbox[0]))
    deduped: List[HeaderAnchor] = []
    for anchor in anchors:
        if not deduped:
            deduped.append(anchor)
            continue
        prev = deduped[-1]
        same_row = abs(anchor.row_y - prev.row_y) <= TABLE_NEARBY_HEADER_Y
        same_x = abs(anchor.bbox[0] - prev.bbox[0]) <= TABLE_NEARBY_HEADER_X
        if same_row and same_x:
            prev_score = len(prev.categories)
            cur_score = len(anchor.categories)
            if cur_score > prev_score or (cur_score == prev_score and len(anchor.text) > len(prev.text)):
                deduped[-1] = anchor
            continue
        deduped.append(anchor)
    return deduped


def _infer_candidate_bbox(
    anchor: HeaderAnchor, words: List[WordBox], frame_size: Tuple[int, int]
) -> Tuple[float, float, float, float]:
    frame_w, frame_h = frame_size
    x0, y0, x1, y1 = anchor.bbox
    left = max(0.0, x0 - TABLE_HEADER_X_MARGIN)
    right = min(float(frame_w), x1 + TABLE_HEADER_RIGHT_MARGIN)
    top = max(0.0, y0 - TABLE_HEADER_TOP_MARGIN)
    max_bottom = min(float(frame_h), y1 + TABLE_MAX_SCAN_HEIGHT)
    scan_bottom = min(float(frame_h), max_bottom + TABLE_SCAN_BOTTOM_TOLERANCE)
    nearby = [
        w
        for w in words
        if (left - 20.0) <= w.cx <= (right + 20.0)
        and (y0 - 10.0) <= w.cy
        and (
            w.cy <= scan_bottom
            or (w.bbox[1] <= scan_bottom <= w.bbox[3])
        )
    ]
    if nearby:
        left = max(0.0, min(left, min(w.bbox[0] for w in nearby) - 12.0))
        right = min(float(frame_w), max(right, max(w.bbox[2] for w in nearby) + 12.0))
        bottom = min(float(frame_h), max(max(w.bbox[3] for w in nearby) + 20.0, y1 + 80.0))
    else:
        bottom = min(float(frame_h), y1 + 220.0)
    bottom = max(bottom, y1 + TABLE_MIN_HEIGHT)
    return (left, top, right, bottom)


def _merge_close_candidates(candidates: List[TableCandidate]) -> List[TableCandidate]:
    merged: List[TableCandidate] = []
    for candidate in sorted(candidates, key=lambda c: (c.header_y, c.bbox[0])):
        if not merged:
            merged.append(candidate)
            continue
        last = merged[-1]
        near_header = (
            abs(candidate.header_y - last.header_y) <= TABLE_NEARBY_HEADER_Y
            and abs(candidate.bbox[0] - last.bbox[0]) <= TABLE_NEARBY_HEADER_X
        )
        overlap = _bbox_iou(candidate.bbox, last.bbox) >= TABLE_MERGE_IOU
        if near_header or overlap:
            union_bbox = _bbox_union(candidate.bbox, last.bbox)
            preferred_text = candidate.header_text if len(candidate.header_text) > len(last.header_text) else last.header_text
            merged[-1] = TableCandidate(
                bbox=union_bbox,
                header_y=min(candidate.header_y, last.header_y),
                header_text=preferred_text,
                categories=tuple(sorted(set(candidate.categories) | set(last.categories))),
            )
            continue
        merged.append(candidate)
    return merged


def detect_table_candidates_from_page_words(
    words: List[WordBox], frame_size: Tuple[int, int], y_cluster: float = HEADER_Y_CLUSTER
) -> List[TableCandidate]:
    anchors = detect_header_anchors(words, y_cluster=y_cluster)
    if not anchors:
        return []

    candidates: List[TableCandidate] = []
    for anchor in anchors:
        bbox = _infer_candidate_bbox(anchor, words, frame_size)
        if (bbox[2] - bbox[0]) < TABLE_MIN_WIDTH:
            continue
        if (bbox[3] - bbox[1]) < TABLE_MIN_HEIGHT:
            continue
        candidates.append(
            TableCandidate(
                bbox=bbox,
                header_y=anchor.row_y,
                header_text=anchor.text,
                categories=anchor.categories,
            )
        )
    if not candidates:
        return []

    candidates = _merge_close_candidates(candidates)
    for idx, base in enumerate(candidates):
        bx0, by0, bx1, by1 = base.bbox
        next_top = by1
        for later in candidates[idx + 1:]:
            if later.header_y <= base.header_y:
                continue
            if _x_overlap_ratio(base.bbox, later.bbox) < 0.2:
                continue
            candidate_top = later.bbox[1]
            if candidate_top < next_top:
                next_top = candidate_top
        if next_top < by1:
            clipped = (bx0, by0, bx1, max(by0 + TABLE_MIN_HEIGHT, next_top - 6.0))
            candidates[idx] = TableCandidate(
                bbox=clipped,
                header_y=base.header_y,
                header_text=base.header_text,
                categories=base.categories,
            )
    return sorted(candidates, key=lambda c: (c.header_y, c.bbox[0]))


def median_or_none(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(median(values))


def infer_column_bounds(words: List[WordBox], side_width: int) -> ColumnBounds:
    clusters = cluster_by_y(words, HEADER_Y_CLUSTER)
    if not clusters:
        centers = [side_width * r for r in DEFAULT_CENTER_RATIOS]
        return build_bounds_from_centers(centers, header_y=0.0, side_width=side_width)

    best = None
    best_tuple = (-1, float("inf"))
    for c in clusters:
        s = header_score(c)
        key = (-s, c.row_y)
        if key < best_tuple:
            best_tuple = key
            best = c

    if best is None or header_score(best) < 2:
        centers = [side_width * r for r in DEFAULT_CENTER_RATIOS]
        return build_bounds_from_centers(centers, header_y=0.0, side_width=side_width)

    header_words = sorted(best.words, key=lambda w: w.cx)

    header_x_max = side_width * 0.55

    def find_x(
        predicate,
        *,
        x_min: Optional[float] = None,
        x_max: Optional[float] = None,
        pick: str = "median",
    ) -> Optional[float]:
        values = []
        for w in header_words:
            t = normalize_text(w.text).lower()
            if x_min is not None and w.cx <= x_min:
                continue
            if x_max is not None and w.cx >= x_max:
                continue
            if predicate(t):
                values.append(w.cx)
        if not values:
            return None
        if pick == "min":
            return float(min(values))
        if pick == "max":
            return float(max(values))
        return median_or_none(values)

    c1 = find_x(lambda t: "記号" in t, x_max=header_x_max, pick="max")
    if c1 is None:
        c1 = find_x(lambda t: "機器" in t, x_max=header_x_max, pick="max")

    c2 = find_x(
        lambda t: "名称" in t,
        x_min=(c1 or 0) + 60,
        x_max=header_x_max,
        pick="max",
    )
    if c2 is None:
        c2 = find_x(
            lambda t: ("名" in t or "称" in t),
            x_min=(c1 or 0) + 60,
            x_max=header_x_max,
            pick="median",
        )

    c3 = find_x(
        lambda t: "v" in t or "電圧" in t or t == "電",
        x_min=(c2 or 0) + 20,
        x_max=header_x_max,
        pick="min",
    )

    c4 = find_x(
        lambda t: "kw" in t,
        x_min=(c3 or 0) + 20,
        x_max=header_x_max,
        pick="min",
    )
    if c4 is None:
        c4 = find_x(
            lambda t: "容量" in t,
            x_min=(c3 or 0) + 20,
            x_max=header_x_max,
            pick="min",
        )

    c5 = find_x(
        lambda t: (
            "配管" in t
            or "配線" in t
            or "サイズ" in t
            or "size" in t
            or t in {"配", "線", "サ", "ズ"}
        ),
        x_min=(c4 or 0) + 30,
        pick="min",
    )

    centers = [c1, c2, c3, c4]
    for i, default_ratio in enumerate(DEFAULT_CENTER_RATIOS):
        if centers[i] is None:
            centers[i] = side_width * default_ratio

    c1f, c2f, c3f, c4f = [float(x) for x in centers]
    if c2f <= c1f + 40:
        c2f = c1f + 120
    if c3f <= c2f + 30:
        c3f = c2f + 90
    if c4f <= c3f + 20:
        c4f = c3f + 80

    bounds = build_bounds_from_centers(
        [c1f, c2f, c3f, c4f],
        header_y=best.row_y,
        side_width=side_width,
    )
    if c5 is not None and float(c5) > (c4f + 35.0):
        right_guard = (c4f + float(c5)) / 2.0
        if right_guard > bounds.b34 + 15.0:
            bounds.x_max = min(bounds.x_max, right_guard)
    return bounds


def build_bounds_from_centers(
    centers: List[float], header_y: float, side_width: int
) -> ColumnBounds:
    c1, c2, c3, c4 = centers
    b12 = (c1 + c2) / 2.0
    b23 = (c2 + c3) / 2.0
    b34 = (c3 + c4) / 2.0

    x_min = max(0.0, c1 - 90.0)
    x_max = min(float(side_width), c4 + 90.0)
    if x_max <= b34:
        x_max = min(float(side_width), b34 + 60.0)

    return ColumnBounds(
        x_min=x_min,
        b12=b12,
        b23=b23,
        b34=b34,
        x_max=x_max,
        header_y=header_y,
    )


def assign_column(x: float, bounds: ColumnBounds) -> Optional[str]:
    if x < bounds.x_min or x > bounds.x_max:
        return None
    if x < bounds.b12:
        return CORE_COLUMNS[0]
    if x < bounds.b23:
        return CORE_COLUMNS[1]
    if x < bounds.b34:
        return CORE_COLUMNS[2]
    return CORE_COLUMNS[3]


def clean_cell(text: str) -> str:
    text = normalize_text(text)
    text = text.strip().replace(" ", "")
    return text.strip("|,:;[]()")


def contains_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-龥]", text))


def normalize_power_text(power: str) -> str:
    power_norm = normalize_text(power).replace(" ", "").replace("　", "")
    power_norm = power_norm.replace(",", "")
    if not power_norm:
        return ""
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", power_norm):
        first_number = re.search(r"[+-]?\d+(?:\.\d+)?", power_norm)
        if first_number is None:
            return ""
        power_norm = first_number.group(0)
    if "." not in power_norm:
        return power_norm

    fractional = power_norm.split(".", 1)[1]
    # Keep normal precision values (e.g. 9.0, 0.75, 0.535) as-is.
    if len(fractional) <= 3:
        return power_norm

    # OCR occasionally appends noise digits (e.g. 0.75255). Round only these over-precision values.
    try:
        rounded = Decimal(power_norm).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return power_norm
    rounded_text = format(rounded, "f")
    if "." in rounded_text:
        rounded_text = rounded_text.rstrip("0").rstrip(".")
    return rounded_text


def normalize_voltage_text(volt: str) -> str:
    volt_norm = normalize_text(volt).upper().replace(" ", "").replace("　", "")
    if not volt_norm:
        return ""
    if volt_norm == "1/200":
        return "1φ200"

    digit_only = "".join(ch for ch in volt_norm if ch.isdigit())
    if re.search(r"3[Φφ/\+\$＊*]?200", volt_norm):
        return "200"
    if digit_only in {"3200", "34200", "36200", "30200"}:
        return "200"
    if digit_only == "200":
        return "200"

    simple = re.fullmatch(r"([+-]?\d+)(?:V)?", volt_norm)
    if simple:
        return simple.group(1)
    return volt_norm


def normalize_row_cells(row: Dict[str, str]) -> Dict[str, str]:
    code = row["機器番号"]
    name = row["機器名称"]
    volt = row["電圧(V)"]
    power = row["容量(kW)"]

    code_upper = normalize_text(code).upper()
    code_match = re.match(r"^([A-Z]{1,4}-[A-Z0-9-]{1,12})(.+)$", code_upper)
    if code_match:
        pure_code = code_match.group(1)
        tail = normalize_text(code)[len(pure_code) :]
        if tail and (contains_japanese(tail) or any(k in tail for k in ROW_FILTER_NAME_KEYWORDS)):
            code = pure_code
            name = f"{tail}{name}"

    # OCRで機器番号末尾に混入しやすい英字(例: EF-B2-2A)を補正
    code_upper = normalize_text(code).upper()
    if not name:
        m = re.match(r"^([A-Z]{1,4}-[A-Z0-9-]*\d)[A-Z]{1,2}$", code_upper)
        if m:
            code = m.group(1)
            code_upper = code

    if code and not re.search(r"[A-Z]{1,4}-[A-Z0-9]{1,6}", normalize_text(code).upper()):
        if name:
            name = f"{code}{name}"
            code = ""
            code_upper = ""

    # OCRノイズで機器名称の先頭に記号が混入するケースを除去
    name = re.sub(r"^[\.,，．。・･·:：;；]+", "", name)

    if name.startswith("-"):
        name = name.lstrip("-")

    # 機器番号の接頭辞から名称を補完
    if not name:
        if code_upper.startswith("EF-") or code_upper.startswith("F-"):
            name = "排風機"
        elif code_upper.startswith("SF-"):
            name = "送風機"
        elif code_upper.startswith("PAC-"):
            name = "空調室内機"

    name = name.replace("室內", "室内")
    if name.startswith("湧水ポンプ") or name.startswith("清水ポンプ"):
        # OCRノイズで末尾に余分な文字が混ざるケースを正規化
        name = "清水ポンプ"

    volt = normalize_voltage_text(volt)
    power = normalize_power_text(power)

    return {
        "機器番号": clean_cell(code),
        "機器名称": clean_cell(name),
        "電圧(V)": clean_cell(volt),
        "容量(kW)": clean_cell(power),
    }


def is_header_row(normalized: str) -> bool:
    lowered = normalized.lower()
    return sum(1 for k in HEADER_KEYWORDS if k in lowered) >= 3


def is_footer_row(normalized: str) -> bool:
    lowered = normalized.lower()
    return any(k in lowered for k in FOOTER_KEYWORDS)


def is_data_row(row: Dict[str, str]) -> bool:
    code = normalize_text(row["機器番号"]).upper()
    name = normalize_text(row["機器名称"])
    volt = normalize_text(row["電圧(V)"])
    power = normalize_text(row["容量(kW)"])
    combined = (code + name + volt + power).lower()
    has_code = bool(re.search(r"[A-Z]{1,4}-[A-Z0-9]{1,6}", code))
    has_name = bool(name)
    has_voltage_num = bool(re.search(r"\d", volt))
    has_power_num = bool(re.search(r"\d", power))

    if not combined:
        return False
    if is_header_row(combined):
        return False
    if any(kw in combined for kw in ["盤姿図", "主開閉器", "トリップ", "ロック連動"]):
        return False

    # Guard against plan/location labels (e.g. SL-6, L-H2) with no table values.
    if has_code and not (has_name or has_voltage_num or has_power_num):
        return False
    # Guard against room labels without numeric table values.
    if has_name and not has_code and not has_voltage_num and not has_power_num:
        return False

    if has_code and (has_name or has_voltage_num or has_power_num):
        return True
    if any(k in name for k in ROW_FILTER_NAME_KEYWORDS) and (has_voltage_num or has_power_num):
        return True
    if "同上用フロートスイッチ" in name or "操作電源" in name:
        return True
    if has_name and has_voltage_num:
        return True
    if has_name and has_power_num:
        return True

    return False


def _split_cluster_by_x_gap(cluster: RowCluster, max_gap: float) -> List[RowCluster]:
    if not cluster.words:
        return []
    words = sorted(cluster.words, key=lambda w: w.cx)
    grouped: List[List[WordBox]] = [[words[0]]]
    prev = words[0]
    for word in words[1:]:
        gap = word.bbox[0] - prev.bbox[2]
        if gap > max_gap:
            grouped.append([word])
        else:
            grouped[-1].append(word)
        prev = word

    split_clusters: List[RowCluster] = []
    for group in grouped:
        split_clusters.append(
            RowCluster(
                row_y=sum(w.cy for w in group) / max(1, len(group)),
                words=group,
            )
        )
    return split_clusters


def infer_dynamic_data_start_y(words: List[WordBox], header_y: float) -> float:
    header_words = [w for w in words if abs(w.cy - header_y) <= HEADER_Y_CLUSTER]
    if not header_words:
        return header_y + TABLE_DEFAULT_START_OFFSET
    header_bottom = max(w.bbox[3] for w in header_words)
    heights = [max(1.0, w.bbox[3] - w.bbox[1]) for w in header_words]
    median_height = float(median(heights)) if heights else 0.0
    offset = min(TABLE_MAX_START_OFFSET, max(TABLE_MIN_START_OFFSET, median_height * 1.2))
    return header_bottom + offset


def rows_from_words(
    words: List[WordBox],
    bounds: ColumnBounds,
    y_cluster: float,
    start_y: Optional[float] = None,
    trailing_non_data_gap: int = TABLE_TRAILING_NON_DATA_GAP,
) -> List[Dict[str, object]]:
    return _rows_from_words_with_meta(
        words,
        bounds,
        y_cluster,
        start_y=start_y,
        trailing_non_data_gap=trailing_non_data_gap,
    ).rows


def _rows_from_words_with_meta(
    words: List[WordBox],
    bounds: ColumnBounds,
    y_cluster: float,
    start_y: Optional[float] = None,
    trailing_non_data_gap: int = TABLE_TRAILING_NON_DATA_GAP,
) -> RowsFromWordsResult:
    if start_y is None:
        start_y = bounds.header_y + DATA_START_OFFSET
    all_clusters = cluster_by_y(words, y_cluster)
    clusters = [
        cluster
        for cluster in all_clusters
        if (
            cluster.row_y >= start_y
            or (
                min(w.bbox[1] for w in cluster.words) <= start_y
                <= max(w.bbox[3] for w in cluster.words)
            )
        )
    ]

    rows: List[Dict[str, object]] = []
    row_idx = 1
    saw_data = False
    last_data_cluster_bottom: Optional[float] = None
    trailing_non_data_count = 0
    stopped_by_footer = False
    for cluster in clusters:
        cols: Dict[str, List[WordBox]] = {c: [] for c in CORE_COLUMNS}
        for w in cluster.words:
            col = assign_column(w.cx, bounds)
            if col is not None:
                cols[col].append(w)

        if all(not cols[c] for c in CORE_COLUMNS):
            continue

        power_words = sorted(cols["容量(kW)"], key=lambda x: x.cx)
        if power_words:
            cluster_heights = [max(1.0, w.bbox[3] - w.bbox[1]) for w in cluster.words]
            median_height = float(median(cluster_heights)) if cluster_heights else 0.0
            if median_height > 0:
                max_noise_height = max(36.0, median_height * 2.2)
                power_words = [
                    w
                    for w in power_words
                    if not (
                        (w.bbox[3] - w.bbox[1]) > max_noise_height
                        and re.fullmatch(r"\d{2,}", normalize_text(w.text).replace(" ", ""))
                    )
                ] or power_words

        row = {
            "機器番号": clean_cell("".join(w.text for w in sorted(cols["機器番号"], key=lambda x: x.cx))),
            "機器名称": clean_cell("".join(w.text for w in sorted(cols["機器名称"], key=lambda x: x.cx))),
            "電圧(V)": clean_cell("".join(w.text for w in sorted(cols["電圧(V)"], key=lambda x: x.cx))),
            "容量(kW)": clean_cell("".join(w.text for w in power_words)),
        }
        row = normalize_row_cells(row)

        normalized = normalize_text("".join(row.values()))
        if is_footer_row(normalized):
            stopped_by_footer = True
            break
        if is_header_row(normalized):
            if saw_data:
                trailing_non_data_count += 1
                if trailing_non_data_count > trailing_non_data_gap:
                    break
            continue
        if not is_data_row(row):
            if saw_data:
                trailing_non_data_count += 1
                if trailing_non_data_count > trailing_non_data_gap:
                    break
            continue

        saw_data = True
        trailing_non_data_count = 0
        last_data_cluster_bottom = max(w.bbox[3] for w in cluster.words)
        rows.append(
            {
                "row_index": row_idx,
                "row_y": round(cluster.row_y, 2),
                **row,
            }
        )
        row_idx += 1

    return RowsFromWordsResult(
        rows=rows,
        saw_data=saw_data,
        last_data_cluster_bottom=last_data_cluster_bottom,
        trailing_non_data_count=trailing_non_data_count,
        stopped_by_footer=stopped_by_footer,
    )


def save_debug_image(
    side_image: Image.Image,
    words: List[WordBox],
    bounds: ColumnBounds,
    out_path: Path,
    data_start_y: Optional[float] = None,
) -> None:
    debug = side_image.convert("RGB")
    draw = ImageDraw.Draw(debug)

    for x in [bounds.x_min, bounds.b12, bounds.b23, bounds.b34, bounds.x_max]:
        draw.line([(x, 0), (x, debug.height)], fill=(255, 120, 0), width=2)
    if data_start_y is None:
        data_start_y = bounds.header_y + DATA_START_OFFSET
    draw.line(
        [(0, data_start_y), (debug.width, data_start_y)],
        fill=(0, 180, 255),
        width=2,
    )

    for w in words:
        col = assign_column(w.cx, bounds)
        if col is None:
            continue
        x0, y0, x1, y1 = w.bbox
        draw.rectangle((x0, y0, x1, y1), outline=(80, 220, 120), width=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug.save(out_path)


def save_header_debug_image(
    page_image: Image.Image,
    headers: List[HeaderAnchor],
    out_path: Path,
) -> None:
    debug = page_image.convert("RGB")
    draw = ImageDraw.Draw(debug)
    for header in headers:
        x0, y0, x1, y1 = header.bbox
        draw.rectangle((x0, y0, x1, y1), outline=(255, 180, 0), width=3)
        draw.text((x0, max(0.0, y0 - 14.0)), "/".join(header.categories), fill=(255, 120, 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug.save(out_path)


def save_table_candidates_debug_image(
    page_image: Image.Image,
    candidates: List[TableCandidate],
    out_path: Path,
) -> None:
    debug = page_image.convert("RGB")
    draw = ImageDraw.Draw(debug)
    for index, candidate in enumerate(candidates, start=1):
        x0, y0, x1, y1 = candidate.bbox
        draw.rectangle((x0, y0, x1, y1), outline=(120, 220, 80), width=3)
        draw.text((x0, max(0.0, y0 - 14.0)), f"T{index}", fill=(60, 180, 60))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug.save(out_path)


def _rescale_words(words: List[WordBox], scale: float) -> List[WordBox]:
    if scale <= 1.0:
        return words
    scaled: List[WordBox] = []
    for w in words:
        x0, y0, x1, y1 = w.bbox
        scaled.append(
            WordBox(
                text=w.text,
                cx=w.cx / scale,
                cy=w.cy / scale,
                bbox=(x0 / scale, y0 / scale, x1 / scale, y1 / scale),
            )
        )
    return scaled


def ocr_table_crop(
    client: vision.ImageAnnotatorClient,
    crop_image: Image.Image,
) -> List[WordBox]:
    if crop_image.width < 1 or crop_image.height < 1:
        return []
    scale = 1.0
    if crop_image.width < 900:
        scale = min(3.0, max(1.0, 900.0 / float(crop_image.width)))
    if scale > 1.0:
        resized = crop_image.resize(
            (int(crop_image.width * scale), int(crop_image.height * scale)),
            resample=RESAMPLE_LANCZOS,
        )
        words = extract_words(client, resized)
        return _rescale_words(words, scale)
    return extract_words(client, crop_image)


def parse_table_candidate(
    *,
    client: vision.ImageAnnotatorClient,
    page_image: Image.Image,
    candidate: TableCandidate,
    table_index: int,
    y_cluster: float,
    debug_dir: Path,
    page_number: int,
) -> TableParseResult:
    x0, y0, x1, y1 = candidate.bbox
    page_w = float(page_image.width)
    page_h = float(page_image.height)
    left = max(0.0, x0)
    top = max(0.0, y0)
    right = min(page_w, x1)
    initial_bottom = min(page_h, y1)
    initial_height = max(1.0, y1 - y0)
    max_bottom = min(page_h, y1 + (initial_height * TABLE_BOTTOM_EXPAND_MAX_RATIO))

    current_bottom = initial_bottom
    expand_attempts = 0
    no_growth_streak = 0
    prev_row_count: Optional[int] = None

    final_crop_bbox = (
        int(left),
        int(top),
        int(right),
        int(initial_bottom),
    )
    final_crop_image: Optional[Image.Image] = None
    final_words: List[WordBox] = []
    final_bounds: Optional[ColumnBounds] = None
    final_start_y: Optional[float] = None
    rows_result = RowsFromWordsResult(
        rows=[],
        saw_data=False,
        last_data_cluster_bottom=None,
        trailing_non_data_count=0,
        stopped_by_footer=False,
    )

    for attempt in range(TABLE_BOTTOM_EXPAND_MAX_TRIES + 1):
        crop_bbox = (
            int(left),
            int(top),
            int(right),
            int(min(page_h, current_bottom)),
        )
        if crop_bbox[2] <= crop_bbox[0] or crop_bbox[3] <= crop_bbox[1]:
            break
        crop_image = page_image.crop(crop_bbox)
        words = ocr_table_crop(client, crop_image)

        bounds: Optional[ColumnBounds] = None
        start_y: Optional[float] = None
        if words:
            bounds = infer_column_bounds(words, crop_image.width)
            start_y = infer_dynamic_data_start_y(words, bounds.header_y)
            rows_result = _rows_from_words_with_meta(words, bounds, y_cluster, start_y=start_y)
        else:
            rows_result = RowsFromWordsResult(
                rows=[],
                saw_data=False,
                last_data_cluster_bottom=None,
                trailing_non_data_count=0,
                stopped_by_footer=False,
            )

        final_crop_bbox = crop_bbox
        final_crop_image = crop_image
        final_words = words
        final_bounds = bounds
        final_start_y = start_y

        if rows_result.stopped_by_footer:
            break
        if attempt >= TABLE_BOTTOM_EXPAND_MAX_TRIES:
            break
        if float(crop_bbox[3]) >= page_h:
            break

        near_bottom_edge = False
        near_edge_threshold = max(TABLE_BOTTOM_NEAR_EDGE_PX, float(y_cluster) * 3.0)
        if rows_result.last_data_cluster_bottom is not None:
            last_data_bottom_on_page = float(crop_bbox[1]) + rows_result.last_data_cluster_bottom
            near_bottom_edge = (float(crop_bbox[3]) - last_data_bottom_on_page) <= near_edge_threshold
        unstable_tail = rows_result.trailing_non_data_count >= TABLE_TRAILING_NON_DATA_GAP
        should_expand = rows_result.saw_data and (near_bottom_edge or unstable_tail)
        if not should_expand:
            break

        row_count = len(rows_result.rows)
        if prev_row_count is not None and row_count <= prev_row_count:
            no_growth_streak += 1
        else:
            no_growth_streak = 0
        prev_row_count = row_count
        # When the last data row is still touching the crop bottom, keep extending
        # even if row count has not increased yet; tails may appear after extra steps.
        if no_growth_streak >= TABLE_BOTTOM_EXPAND_NO_GROWTH_STREAK and not near_bottom_edge:
            break

        next_bottom = min(max_bottom, float(crop_bbox[3]) + TABLE_BOTTOM_EXPAND_STEP_PX)
        if next_bottom <= float(crop_bbox[3]):
            break
        current_bottom = next_bottom
        expand_attempts += 1

    rows = list(rows_result.rows)
    for row in rows:
        row["row_y"] = round(float(row["row_y"]) + float(final_crop_bbox[1]), 2)

    if final_crop_image is not None and final_words and final_bounds is not None:
        save_debug_image(
            final_crop_image,
            final_words,
            final_bounds,
            debug_dir / f"p{page_number}_table{table_index}.png",
            data_start_y=final_start_y,
        )
    return TableParseResult(
        table_index=table_index,
        candidate=candidate,
        rows=rows,
        expand_attempts=expand_attempts,
        final_crop_bottom=float(final_crop_bbox[3]),
    )


def extract_page_rows_v3(
    *,
    client: vision.ImageAnnotatorClient,
    page_image: Image.Image,
    y_cluster: float,
    debug_dir: Path,
    page_number: int,
) -> Dict[str, object]:
    page_words = extract_words(client, page_image)
    headers = detect_header_anchors(page_words)
    candidates = detect_table_candidates_from_page_words(page_words, page_image.size)

    save_header_debug_image(page_image, headers, debug_dir / f"p{page_number}_headers.png")
    save_table_candidates_debug_image(page_image, candidates, debug_dir / f"p{page_number}_tables.png")

    parsed_tables: List[TableParseResult] = []
    page_rows: List[Dict[str, object]] = []
    row_index = 1
    for table_index, candidate in enumerate(candidates, start=1):
        parsed = parse_table_candidate(
            client=client,
            page_image=page_image,
            candidate=candidate,
            table_index=table_index,
            y_cluster=y_cluster,
            debug_dir=debug_dir,
            page_number=page_number,
        )
        parsed_tables.append(parsed)
        for row in parsed.rows:
            page_rows.append(
                {
                    "row_index": row_index,
                    "row_y": row["row_y"],
                    "side": f"T{table_index:02d}",
                    "table_index": table_index,
                    "機器番号": row["機器番号"],
                    "機器名称": row["機器名称"],
                    "電圧(V)": row["電圧(V)"],
                    "容量(kW)": row["容量(kW)"],
                }
            )
            row_index += 1
    return {
        "rows": page_rows,
        "page_words": page_words,
        "headers": headers,
        "candidates": candidates,
        "tables": parsed_tables,
    }


def legacy_side_split_extract_page(
    *,
    client: vision.ImageAnnotatorClient,
    page_image: Image.Image,
    y_cluster: float,
    debug_dir: Path,
    page_number: int,
) -> Dict[str, object]:
    right_side_words: List[WordBox] = []
    right_side_size = (0, 0)
    page_rows: List[Dict[str, object]] = []
    sides = split_sides(page_image)
    for side in ["L", "R"]:
        side_image = sides[side]
        words = extract_words(client, side_image)
        if not words:
            continue
        if side == "R":
            right_side_words = words
            right_side_size = side_image.size
        bounds = infer_column_bounds(words, side_image.width)
        rows = rows_from_words(
            words,
            bounds,
            y_cluster,
            trailing_non_data_gap=LEGACY_TRAILING_NON_DATA_GAP,
        )
        for row in rows:
            row["side"] = side
            page_rows.append(row)
        save_debug_image(
            side_image,
            words,
            bounds,
            debug_dir / f"bbox_p{page_number}_{side}.png",
        )
    return {
        "rows": page_rows,
        "right_side_words": right_side_words,
        "right_side_size": right_side_size,
    }


def write_csv(rows: List[Dict[str, object]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = OUTPUT_COLUMNS
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_vision_client(service_account_json: str) -> vision.ImageAnnotatorClient:
    if _VISION_IMPORT_ERROR is not None:
        raise ImportError(f"google-cloud-vision is not available: {_VISION_IMPORT_ERROR}")
    if not service_account_json:
        raise ValueError("VISION_SERVICE_ACCOUNT_KEY is not set.")
    info = json.loads(service_account_json)
    creds = service_account.Credentials.from_service_account_info(info)
    return vision.ImageAnnotatorClient(credentials=creds)


def extract_raster_pdf(
    pdf_path: Path,
    out_csv: Path,
    debug_dir: Path,
    vision_service_account_json: str,
    page: int = 1,
    dpi: int = 300,
    y_cluster: float = 20.0,
) -> Dict[str, object]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"入力PDFが見つかりません: {pdf_path}")

    client = build_vision_client(vision_service_account_json)
    all_rows: List[Dict[str, object]] = []
    total_pages = count_pdf_pages(pdf_path)
    target_pages = resolve_target_pages(total_pages=total_pages, page=page)
    drawing_number_by_page: Dict[int, str] = {}
    drawing_number_source_by_page: Dict[int, str] = {}
    rows_by_page: Dict[int, int] = {}
    tables_detected_by_page: Dict[int, int] = {}
    table_count_by_page: Dict[int, int] = {}
    fallback_pages: List[int] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for target_page in target_pages:
            png_path = run_pdftoppm(pdf_path, target_page, dpi, tmp_dir)
            page_image = Image.open(png_path).convert("RGB")
            right_side_words: List[WordBox] = []
            right_side_size = page_image.size
            page_rows: List[Dict[str, object]] = []

            if target_page in LEGACY_FIRST_PAGES:
                tables_detected_by_page[target_page] = 0
                table_count_by_page[target_page] = 0
                legacy_result = legacy_side_split_extract_page(
                    client=client,
                    page_image=page_image,
                    y_cluster=y_cluster,
                    debug_dir=debug_dir,
                    page_number=target_page,
                )
                page_rows = list(legacy_result["rows"])
                right_side_words = list(legacy_result["right_side_words"])
                right_side_size = tuple(legacy_result["right_side_size"])  # type: ignore[arg-type]
                if not page_rows:
                    fallback_pages.append(target_page)
                    v3_result = extract_page_rows_v3(
                        client=client,
                        page_image=page_image,
                        y_cluster=y_cluster,
                        debug_dir=debug_dir,
                        page_number=target_page,
                    )
                    page_rows = list(v3_result["rows"])
                    right_side_words = list(v3_result["page_words"])
                    candidates = list(v3_result["candidates"])
                    tables_detected_by_page[target_page] = len(candidates)
                    table_count_by_page[target_page] = len(candidates)
            else:
                v3_result = extract_page_rows_v3(
                    client=client,
                    page_image=page_image,
                    y_cluster=y_cluster,
                    debug_dir=debug_dir,
                    page_number=target_page,
                )
                page_rows = list(v3_result["rows"])
                right_side_words = list(v3_result["page_words"])
                candidates = list(v3_result["candidates"])
                tables_detected_by_page[target_page] = len(candidates)
                table_count_by_page[target_page] = len(candidates)
                if not page_rows:
                    fallback_pages.append(target_page)
                    legacy_result = legacy_side_split_extract_page(
                        client=client,
                        page_image=page_image,
                        y_cluster=y_cluster,
                        debug_dir=debug_dir,
                        page_number=target_page,
                    )
                    page_rows = list(legacy_result["rows"])
                    right_side_words = list(legacy_result["right_side_words"])
                    right_side_size = tuple(legacy_result["right_side_size"])  # type: ignore[arg-type]

            drawing_number, drawing_number_source = resolve_drawing_number(
                pdf_path=pdf_path,
                page=target_page,
                right_side_words=right_side_words,
                right_side_size=right_side_size,
            )
            drawing_number_by_page[target_page] = drawing_number
            drawing_number_source_by_page[target_page] = drawing_number_source
            rows_by_page[target_page] = len(page_rows)
            for row in page_rows:
                row[DRAWING_NUMBER_COLUMN] = drawing_number
                row["page"] = target_page
                all_rows.append(row)

    summary_drawing_number = ""
    summary_drawing_source = "none"
    for target_page in target_pages:
        candidate = drawing_number_by_page.get(target_page, "")
        if candidate:
            summary_drawing_number = candidate
            summary_drawing_source = drawing_number_source_by_page.get(target_page, "none")
            break
    all_rows.sort(key=lambda r: (int(r.get("page", 0)), str(r["side"]), int(r["row_index"])))
    write_csv(all_rows, out_csv)
    return {
        "rows": len(all_rows),
        "columns": OUTPUT_COLUMNS,
        "output_csv": str(out_csv),
        "debug_dir": str(debug_dir),
        "drawing_number": summary_drawing_number,
        "drawing_number_source": summary_drawing_source,
        "pages_processed": len(target_pages),
        "target_pages": target_pages,
        "drawing_numbers_by_page": drawing_number_by_page,
        "drawing_number_sources_by_page": drawing_number_source_by_page,
        "rows_by_page": rows_by_page,
        "tables_detected_by_page": tables_detected_by_page,
        "table_count_by_page": table_count_by_page,
        "fallback_pages": fallback_pages,
    }


def main() -> int:
    args = parse_args()
    pdf_path = Path(args.pdf)
    out_csv = Path(args.out)
    debug_dir = Path(args.debug_dir)
    y_cluster = float(args.y_cluster)

    if not pdf_path.exists():
        raise FileNotFoundError(f"入力PDFが見つかりません: {pdf_path}")

    # CLI互換: 環境変数GOOGLE_APPLICATION_CREDENTIALSで認証
    client = vision.ImageAnnotatorClient()
    all_rows: List[Dict[str, object]] = []
    total_pages = count_pdf_pages(pdf_path)
    target_pages = resolve_target_pages(total_pages=total_pages, page=args.page)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for target_page in target_pages:
            png_path = run_pdftoppm(pdf_path, target_page, args.dpi, tmp_dir)
            page_image = Image.open(png_path).convert("RGB")
            right_side_words: List[WordBox] = []
            right_side_size = page_image.size
            page_rows: List[Dict[str, object]] = []
            if target_page in LEGACY_FIRST_PAGES:
                legacy_result = legacy_side_split_extract_page(
                    client=client,
                    page_image=page_image,
                    y_cluster=y_cluster,
                    debug_dir=debug_dir,
                    page_number=target_page,
                )
                page_rows = list(legacy_result["rows"])
                right_side_words = list(legacy_result["right_side_words"])
                right_side_size = tuple(legacy_result["right_side_size"])  # type: ignore[arg-type]
                if not page_rows:
                    v3_result = extract_page_rows_v3(
                        client=client,
                        page_image=page_image,
                        y_cluster=y_cluster,
                        debug_dir=debug_dir,
                        page_number=target_page,
                    )
                    page_rows = list(v3_result["rows"])
                    right_side_words = list(v3_result["page_words"])
            else:
                v3_result = extract_page_rows_v3(
                    client=client,
                    page_image=page_image,
                    y_cluster=y_cluster,
                    debug_dir=debug_dir,
                    page_number=target_page,
                )
                page_rows = list(v3_result["rows"])
                right_side_words = list(v3_result["page_words"])
                if not page_rows:
                    legacy_result = legacy_side_split_extract_page(
                        client=client,
                        page_image=page_image,
                        y_cluster=y_cluster,
                        debug_dir=debug_dir,
                        page_number=target_page,
                    )
                    page_rows = list(legacy_result["rows"])
                    right_side_words = list(legacy_result["right_side_words"])
                    right_side_size = tuple(legacy_result["right_side_size"])  # type: ignore[arg-type]

            drawing_number, _ = resolve_drawing_number(
                pdf_path=pdf_path,
                page=target_page,
                right_side_words=right_side_words,
                right_side_size=right_side_size,
            )
            for row in page_rows:
                row[DRAWING_NUMBER_COLUMN] = drawing_number
                row["page"] = target_page
                all_rows.append(row)
    all_rows.sort(key=lambda r: (int(r.get("page", 0)), str(r["side"]), int(r["row_index"])))
    write_csv(all_rows, out_csv)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
