from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Optional, Tuple

from PIL import Image

from extractors.raster_extractor import (
    WordBox,
    build_vision_client,
    cluster_by_y,
    count_pdf_pages,
    extract_words,
    normalize_text,
    resolve_target_pages,
    run_pdftoppm,
)

LABEL_KEYWORDS = (
    "電源電圧",
    "入力電圧",
    "出力電圧",
    "消費電流",
    "消費電力",
    "質量",
    "材質",
    "形状",
    "色調",
    "塗色",
    "塗装",
    "備考",
)
LABEL_KEYWORDS_COMPACT = tuple(item.replace(" ", "") for item in LABEL_KEYWORDS)
TITLE_EXCLUDE_TERMS = LABEL_KEYWORDS_COMPACT + (
    "寸法",
    "注記",
    "取付参考例",
    "図面",
    "縮尺",
)
CODE_PATTERN = re.compile(r"[A-Z]{1,4}-[A-Z0-9]{1,}(?:\+[A-Z0-9-]+)?(?:トク)?")
PRODUCT_CODE_PATTERN = re.compile(r"商品コード[:：]?\s*([0-9A-Za-z-]{4,})")
PAREN_PRODUCT_CODE_PATTERN = re.compile(r"\(商品コード[:：]?[0-9A-Za-z-]{4,}\)")
SPECIAL_IDENTIFIER_TOKENS = ("特注品",)
JAPANESE_PATTERN = re.compile(r"[ぁ-んァ-ン一-龥]")
HEADER_MARKER_PATTERN = re.compile(r"[A-Z]{1,3}\d{1,3}")
TABLE_MIN_LABEL_COUNT = 1
TABLE_MAX_WIDTH_RATIO = 2.1
READING_ORDER_Y_BAND = 140.0
TITLE_MAX_DISTANCE_TO_TABLE = 900.0
CODE_ASSIGN_MAX_SCORE = 420.0
PRODUCT_CODE_ASSIGN_MAX_SCORE = 520.0
CODE_ASSIGN_SOFT_MARGIN = 40.0
CODE_ASSIGN_SOFT_MIN_OVERLAP = 0.70
TITLE_SEGMENT_X_GAP = 40.0
TITLE_CODE_ROW_MIN_DIFF = 25.0
TITLE_CODE_ROW_MAX_DIFF = 70.0
CODE_TARGET_LEFT_MARGIN = 140.0
CODE_TARGET_RIGHT_MARGIN = 220.0
CODE_OVERLAP_PENALTY_WEIGHT = 220.0


@dataclass(frozen=True)
class Segment:
    page: int
    row_y: float
    x0: float
    x1: float
    top: float
    bottom: float
    text: str
    text_compact: str


@dataclass
class TableBlock:
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    segments: List[Segment]


@dataclass
class ParsedTableBlock:
    block: TableBlock
    pairs: List[Tuple[str, str]]
    label_count: int


@dataclass
class FrameRow:
    page: int
    top: float
    x0: float
    title: str
    code: str
    pairs: List[Tuple[str, str]]

    @property
    def values(self) -> List[str]:
        values: List[str] = []
        if self.title:
            values.append(self.title)
        if self.code:
            values.append(self.code)
        for key, value in self.pairs:
            if key:
                values.append(key)
            if value:
                values.append(value)
        return values


def _compact_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").replace(" ", "").replace("　", "")


def _split_row_cluster_by_x_gap(words: List[WordBox], max_gap: float = 70.0) -> List[List[WordBox]]:
    ordered = sorted(words, key=lambda item: item.cx)
    if not ordered:
        return []

    groups: List[List[WordBox]] = [[ordered[0]]]
    prev = ordered[0]
    for word in ordered[1:]:
        gap = word.bbox[0] - prev.bbox[2]
        if gap > max_gap:
            groups.append([word])
        else:
            groups[-1].append(word)
        prev = word
    return groups


def build_segments_from_words(
    words: List[WordBox],
    *,
    page: int,
    y_cluster: float = 12.0,
    x_gap: float = 70.0,
) -> List[Segment]:
    clusters = cluster_by_y(words, y_cluster)
    segments: List[Segment] = []
    for cluster in clusters:
        groups = _split_row_cluster_by_x_gap(cluster.words, max_gap=x_gap)
        for group in groups:
            tokens = [normalize_text(word.text).strip() for word in sorted(group, key=lambda item: item.cx)]
            tokens = [token for token in tokens if token]
            if not tokens:
                continue
            text = " ".join(tokens)
            compact = _compact_text(text).strip("|")
            if not compact:
                continue
            x0 = min(word.bbox[0] for word in group)
            x1 = max(word.bbox[2] for word in group)
            top = min(word.bbox[1] for word in group)
            bottom = max(word.bbox[3] for word in group)
            segments.append(
                Segment(
                    page=page,
                    row_y=float(cluster.row_y),
                    x0=float(x0),
                    x1=float(x1),
                    top=float(top),
                    bottom=float(bottom),
                    text=text,
                    text_compact=compact,
                )
            )
    return sorted(segments, key=lambda item: (item.page, item.row_y, item.x0))


def _x_overlap_ratio(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    left = max(a[0], b[0])
    right = min(a[1], b[1])
    overlap = max(0.0, right - left)
    width_a = max(1.0, a[1] - a[0])
    width_b = max(1.0, b[1] - b[0])
    return overlap / min(width_a, width_b)


def _is_table_segment(segment: Segment) -> bool:
    compact = _normalize_for_label_detection(segment.text_compact)
    return any(keyword in compact for keyword in LABEL_KEYWORDS_COMPACT)


def _is_title_candidate(segment: Segment) -> bool:
    compact = segment.text_compact
    if len(compact) < 3 or len(compact) > 48:
        return False
    if CODE_PATTERN.search(compact):
        return False
    if "商品コード" in compact:
        return False
    if any(token in compact for token in SPECIAL_IDENTIFIER_TOKENS):
        return False
    if not JAPANESE_PATTERN.search(compact):
        return False
    if any(term in compact for term in TITLE_EXCLUDE_TERMS):
        return False
    if "約" in compact and re.search(r"\d", compact):
        return False
    if re.search(r"\d+(?:\.\d+)?(?:kg|g|v|a|w|hz|φ)", compact.lower()):
        return False
    if re.fullmatch(r"[^ぁ-んァ-ン一-龥A-Za-z0-9]+", compact):
        return False
    if compact.startswith(("(", "（", "<", "＜", "[")):
        return False
    return True


def _cluster_table_segments(segments: List[Segment]) -> List[TableBlock]:
    sorted_segments = sorted(segments, key=lambda item: (item.page, item.row_y, item.x0))
    blocks: List[TableBlock] = []
    for segment in sorted_segments:
        matched: TableBlock | None = None
        for block in blocks:
            if block.page != segment.page:
                continue
            if segment.row_y > block.bottom + 140.0:
                continue
            if _x_overlap_ratio((segment.x0, segment.x1), (block.x0, block.x1)) < 0.18:
                continue
            matched = block
            break

        if matched is None:
            blocks.append(
                TableBlock(
                    page=segment.page,
                    x0=segment.x0,
                    x1=segment.x1,
                    top=segment.top,
                    bottom=segment.bottom,
                    segments=[segment],
                )
            )
            continue

        matched.x0 = min(matched.x0, segment.x0)
        matched.x1 = max(matched.x1, segment.x1)
        matched.top = min(matched.top, segment.top)
        matched.bottom = max(matched.bottom, segment.bottom)
        matched.segments.append(segment)
    return blocks


def _find_code_in_segment(segment: Segment) -> str:
    matched = CODE_PATTERN.search(segment.text_compact)
    if matched:
        return matched.group(0)
    paren_product = PAREN_PRODUCT_CODE_PATTERN.search(segment.text_compact)
    if paren_product:
        return paren_product.group(0)
    product = PRODUCT_CODE_PATTERN.search(segment.text_compact)
    if product:
        return f"商品コード:{product.group(1)}"
    for token in SPECIAL_IDENTIFIER_TOKENS:
        if token in segment.text_compact:
            return token
    return ""


def _is_code_candidate_segment(segment: Segment) -> bool:
    code = _find_code_in_segment(segment)
    if not code:
        return False
    compact = segment.text_compact
    if any(keyword in compact for keyword in LABEL_KEYWORDS_COMPACT):
        return False
    if len(compact) > len(code) + 14:
        return False
    return True


def _cluster_y_values(values: List[float], tolerance: float = 24.0) -> List[Tuple[float, int]]:
    if not values:
        return []
    sorted_values = sorted(values)
    clusters: List[List[float]] = [[sorted_values[0]]]
    for value in sorted_values[1:]:
        if abs(value - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [(sum(cluster) / len(cluster), len(cluster)) for cluster in clusters]


def _header_row_centers_from_codes(code_segments: List[Segment]) -> List[float]:
    values = [segment.row_y for segment in code_segments if _is_code_candidate_segment(segment)]
    clusters = _cluster_y_values(values, tolerance=24.0)
    return [center for center, count in clusters if count >= 3]


def _filter_title_candidates_by_header_rows(
    title_candidates: List[Segment],
    code_row_centers: List[float],
) -> List[Segment]:
    if not code_row_centers:
        return title_candidates

    filtered = []
    for segment in title_candidates:
        if any(TITLE_CODE_ROW_MIN_DIFF <= (row_y - segment.row_y) <= TITLE_CODE_ROW_MAX_DIFF for row_y in code_row_centers):
            filtered.append(segment)
    if filtered:
        return filtered
    return title_candidates


def _estimate_header_y_for_block(block: TableBlock, code_row_centers: List[float]) -> float:
    if not code_row_centers:
        return max(0.0, block.top - 420.0)
    usable = [row_y for row_y in code_row_centers if row_y < block.top + 40.0]
    if not usable:
        return max(0.0, block.top - 420.0)
    nearest = max(usable)
    return nearest - 47.0


def _block_key(block: TableBlock) -> Tuple[int, int, int, int]:
    return (block.page, int(round(block.top)), int(round(block.x0)), int(round(block.x1)))


def _pick_title_for_block(
    block: TableBlock,
    title_candidates: List[Segment],
    *,
    min_overlap: float = 0.15,
) -> Segment | None:
    candidates: List[Tuple[float, Segment]] = []
    for segment in title_candidates:
        if segment.page != block.page:
            continue
        if segment.row_y >= block.top:
            continue
        if (block.top - segment.row_y) > TITLE_MAX_DISTANCE_TO_TABLE:
            continue
        overlap = _x_overlap_ratio((segment.x0, segment.x1), (block.x0 - 140.0, block.x1 + 140.0))
        if overlap < min_overlap:
            continue
        block_center = (block.x0 + block.x1) / 2.0
        seg_center = (segment.x0 + segment.x1) / 2.0
        score = (block.top - segment.row_y) + abs(seg_center - block_center) * 0.2
        candidates.append((score, segment))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _pick_code_for_anchor(
    *,
    page: int,
    anchor_x0: float,
    anchor_x1: float,
    anchor_y: float,
    max_y: float,
    code_segments: List[Segment],
    x_pad_left: float = 200.0,
    x_pad_right: float = 300.0,
    min_overlap: float = 0.01,
) -> str:
    candidates: List[Tuple[float, str]] = []
    anchor_center = (anchor_x0 + anchor_x1) / 2.0
    anchor_range = (anchor_x0 - x_pad_left, anchor_x1 + x_pad_right)
    for segment in code_segments:
        if segment.page != page:
            continue
        if not (anchor_y <= segment.row_y <= max_y):
            continue
        code = _find_code_in_segment(segment)
        if not code:
            continue
        overlap = _x_overlap_ratio((segment.x0, segment.x1), anchor_range)
        if overlap < min_overlap:
            continue
        seg_center = (segment.x0 + segment.x1) / 2.0
        score = abs(segment.row_y - anchor_y) * 1.2 + abs(seg_center - anchor_center) + (1.0 - overlap) * 120.0
        candidates.append((score, code))
    if not candidates:
        return ""
    return min(candidates, key=lambda item: item[0])[1]


def _pick_code_for_title(
    *,
    block: TableBlock,
    header_y: float,
    code_segments: List[Segment],
) -> str:
    lower_y = header_y + 18.0
    upper_y = header_y + 190.0
    block_center = (block.x0 + block.x1) / 2.0
    target_range = (block.x0 - CODE_TARGET_LEFT_MARGIN, block.x1 + CODE_TARGET_RIGHT_MARGIN)
    candidates: List[Tuple[float, str, float]] = []

    for segment in code_segments:
        if segment.page != block.page:
            continue
        if not (lower_y <= segment.row_y <= upper_y):
            continue

        code = _find_code_in_segment(segment)
        if not code:
            continue

        is_product_code = "商品コード:" in code
        is_special_identifier = code in SPECIAL_IDENTIFIER_TOKENS
        text = segment.text_compact
        if any(keyword in text for keyword in LABEL_KEYWORDS_COMPACT):
            continue
        overlap = _x_overlap_ratio((segment.x0, segment.x1), target_range)
        if overlap <= 0.0:
            continue

        penalty = 0.0
        if len(text) > len(code) + 12:
            penalty += 120.0
        if re.search(r"[ぁ-んァ-ン一-龥:：]", text) and not (is_product_code or is_special_identifier):
            penalty += 80.0
        if segment.row_y > 3000.0:
            penalty += 200.0
        penalty += (1.0 - overlap) * CODE_OVERLAP_PENALTY_WEIGHT

        seg_center = (segment.x0 + segment.x1) / 2.0
        score = abs(seg_center - block_center) + abs(segment.row_y - lower_y) * 2.0 + penalty
        candidates.append((score, code, overlap))

    if not candidates:
        return ""
    best_score, best_code, best_overlap = min(candidates, key=lambda item: item[0])
    threshold = PRODUCT_CODE_ASSIGN_MAX_SCORE if "商品コード:" in best_code else CODE_ASSIGN_MAX_SCORE
    if best_score > threshold:
        if (
            "商品コード:" not in best_code
            and best_overlap >= CODE_ASSIGN_SOFT_MIN_OVERLAP
            and best_score <= threshold + CODE_ASSIGN_SOFT_MARGIN
        ):
            return best_code
        return ""
    return best_code


def _title_chunks_from_compact(compact: str) -> List[str]:
    markers = list(HEADER_MARKER_PATTERN.finditer(compact))
    if len(markers) < 2:
        return []
    chunks: List[str] = []
    for idx, marker in enumerate(markers):
        start = marker.start()
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(compact)
        raw = compact[start:end]
        cleaned = HEADER_MARKER_PATTERN.sub("", raw, count=1).strip(" |・")
        if cleaned:
            chunks.append(cleaned)
    return chunks


def _normalize_title(title: str) -> str:
    normalized = title.strip("[]|")
    normalized = re.sub(r"^\d+\|", "", normalized)
    normalized = re.sub(r"^[A-Za-z]{1,4}\d{0,3}(?=[ぁ-んァ-ン一-龥（(])", "", normalized)
    normalized = re.sub(r"^[A-Za-z]{1,4}(?=[ぁ-んァ-ン一-龥（(])", "", normalized)
    normalized = re.sub(r"^[◎○●◯◇◆□■△▲▽▼⊙⊗◉]+", "", normalized)
    normalized = normalized.lstrip("|・")
    normalized = normalized.lstrip("@")
    normalized = re.sub(r"スピーカ(?!ー)", "スピーカー", normalized)
    normalized = normalized.replace("(", "（").replace(")", "）").replace("+", "＋")
    return normalized.strip()


def _snap_split_boundary(text: str, index: int) -> int:
    if index <= 0 or index >= len(text):
        return index
    keywords = (
        "セキュリティ",
        "ロビーインターホン",
        "住戸",
        "マグネット",
        "カメラ",
        "電源",
    )
    best = index
    best_distance = 999
    for keyword in keywords:
        start = text.find(keyword)
        while start != -1:
            distance = abs(start - index)
            if distance < best_distance and distance <= 10:
                best = start
                best_distance = distance
            start = text.find(keyword, start + 1)
    return best


def _split_title_text_by_blocks(title_segment: Segment, blocks: List[TableBlock]) -> Dict[Tuple[int, int, int, int], str]:
    if not blocks:
        return {}

    ordered_blocks = sorted(blocks, key=lambda block: block.x0)
    source_text = _normalize_title(title_segment.text_compact)
    if len(ordered_blocks) == 1 or not source_text:
        return {_block_key(ordered_blocks[0]): source_text}

    seg_width = max(1.0, title_segment.x1 - title_segment.x0)
    boundaries = [0]
    for idx in range(len(ordered_blocks) - 1):
        split_x = (ordered_blocks[idx].x1 + ordered_blocks[idx + 1].x0) / 2.0
        ratio = (split_x - title_segment.x0) / seg_width
        ratio = max(0.0, min(1.0, ratio))
        boundaries.append(int(round(ratio * len(source_text))))
    boundaries.append(len(source_text))
    for idx in range(1, len(boundaries) - 1):
        boundaries[idx] = _snap_split_boundary(source_text, boundaries[idx])

    for idx in range(1, len(boundaries)):
        if boundaries[idx] <= boundaries[idx - 1]:
            boundaries[idx] = boundaries[idx - 1] + 1
    boundaries[-1] = len(source_text)

    split_map: Dict[Tuple[int, int, int, int], str] = {}
    for idx, block in enumerate(ordered_blocks):
        start = boundaries[idx]
        end = boundaries[idx + 1]
        if idx == len(ordered_blocks) - 1:
            end = len(source_text)
        chunk = source_text[start:end].strip()
        split_map[_block_key(block)] = _normalize_title(chunk)

    # If split quality is too poor, avoid destructive splitting.
    if any(len(value) < 4 for value in split_map.values()):
        primary_key = _block_key(ordered_blocks[0])
        fallback = {primary_key: source_text}
        for block in ordered_blocks[1:]:
            fallback[_block_key(block)] = ""
        return fallback
    return split_map


def _resolve_title_text_for_block(title_segment: Segment, block: TableBlock) -> str:
    compact = title_segment.text_compact
    chunks = _title_chunks_from_compact(compact)
    if not chunks:
        return _normalize_title(compact)

    seg_width = max(1.0, title_segment.x1 - title_segment.x0)
    block_center = (block.x0 + block.x1) / 2.0
    ratio = (block_center - title_segment.x0) / seg_width
    if ratio < 0.0:
        ratio = 0.0
    if ratio > 0.999999:
        ratio = 0.999999
    index = int(ratio * len(chunks))
    if index >= len(chunks):
        index = len(chunks) - 1
    return _normalize_title(chunks[index])


def _normalize_for_label_detection(value: str) -> str:
    compact = _compact_text(value)
    compact = compact.strip("|")
    compact = compact.replace("電電源電圧", "電源電圧")
    compact = compact.replace("消消費電流", "消費電流")
    compact = compact.replace("消消費電力", "消費電力")
    compact = compact.replace("質本体", "質量本体")
    compact = compact.replace("材貝質", "材質")
    compact = compact.replace("形備状", "形状")
    compact = compact.replace("形備", "形状")
    if compact.startswith("考"):
        compact = f"備{compact}"
    return compact


def _clean_value(value: str) -> str:
    cleaned = value.strip("|:：- ")
    cleaned = cleaned.replace("\u3000", "")
    cleaned = cleaned.replace("黑", "黒")
    return cleaned


def extract_label_value_pairs(text: str) -> List[Tuple[str, str]]:
    normalized = _normalize_for_label_detection(text)
    hits: List[Tuple[int, int, str]] = []
    for label in LABEL_KEYWORDS_COMPACT:
        start = 0
        while True:
            idx = normalized.find(label, start)
            if idx == -1:
                break
            hits.append((idx, idx + len(label), label))
            start = idx + len(label)

    if not hits:
        return []

    hits.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: List[Tuple[int, int, str]] = []
    for hit in hits:
        if selected and hit[0] < selected[-1][1]:
            continue
        selected.append(hit)

    if len(selected) >= 2:
        last_start, last_end, last_label = selected[-1]
        _prev_start, _prev_end, prev_label = selected[-2]
        if last_end >= len(normalized) and last_label == prev_label and last_start < len(normalized):
            selected = selected[:-1]

    pairs: List[Tuple[str, str]] = []
    for idx, (start, end, label) in enumerate(selected):
        value_end = selected[idx + 1][0] if idx + 1 < len(selected) else len(normalized)
        value = _clean_value(normalized[end:value_end])
        pairs.append((label, value))

    merged: List[Tuple[str, str]] = []
    for label, value in pairs:
        if merged and merged[-1][0] == label:
            prev_label, prev_value = merged[-1]
            if not value:
                merged[-1] = (prev_label, _clean_value(prev_value))
                continue
            if not prev_value:
                merged[-1] = (prev_label, value)
                continue
        merged.append((label, value))
    return merged


def _is_continuation_text(text: str) -> bool:
    compact = _normalize_for_label_detection(text)
    if not compact:
        return False
    if CODE_PATTERN.search(compact):
        return False
    if extract_label_value_pairs(compact):
        return False
    if len(compact) > 80:
        return False
    return bool(JAPANESE_PATTERN.search(compact) or re.search(r"\d", compact))


def _extract_pairs_from_block(block: TableBlock) -> Tuple[List[Tuple[str, str]], int]:
    pairs: List[Tuple[str, str]] = []
    for segment in sorted(block.segments, key=lambda item: (item.row_y, item.x0)):
        detected = extract_label_value_pairs(segment.text_compact)
        if detected:
            pairs.extend(detected)
            continue
        if pairs and _is_continuation_text(segment.text_compact):
            label, prev = pairs[-1]
            pairs[-1] = (label, _clean_value(prev + segment.text_compact))

    filtered = [(label, value) for label, value in pairs if label]
    labels = {label for label, _ in filtered}
    return filtered, len(labels)


def _attach_continuation_segments_to_blocks(blocks: List[TableBlock], segments: List[Segment]) -> None:
    if not blocks:
        return

    signatures_by_block: Dict[int, set[Tuple[float, float, float, str]]] = {}
    for idx, block in enumerate(blocks):
        signatures_by_block[idx] = {
            (segment.row_y, segment.x0, segment.x1, segment.text_compact)
            for segment in block.segments
        }

    for idx, block in enumerate(blocks):
        known = signatures_by_block[idx]
        for segment in segments:
            signature = (segment.row_y, segment.x0, segment.x1, segment.text_compact)
            if signature in known:
                continue
            if segment.page != block.page:
                continue
            if segment.row_y < block.top - 8.0 or segment.row_y > block.bottom + 40.0:
                continue
            if _x_overlap_ratio((segment.x0, segment.x1), (block.x0, block.x1)) < 0.35:
                continue
            if HEADER_MARKER_PATTERN.search(segment.text_compact):
                continue
            if _is_title_candidate(segment):
                continue
            if _find_code_in_segment(segment):
                continue
            if not _is_continuation_text(segment.text_compact):
                continue
            block.segments.append(segment)
            known.add(signature)
            block.x0 = min(block.x0, segment.x0)
            block.x1 = max(block.x1, segment.x1)
            block.top = min(block.top, segment.top)
            block.bottom = max(block.bottom, segment.bottom)


def _filter_extreme_wide_blocks(blocks: List[ParsedTableBlock]) -> List[ParsedTableBlock]:
    if len(blocks) < 2:
        return blocks

    widths = [max(1.0, block.block.x1 - block.block.x0) for block in blocks]
    sorted_widths = sorted(widths)
    median = sorted_widths[len(sorted_widths) // 2]
    max_width = median * TABLE_MAX_WIDTH_RATIO
    return [block for block, width in zip(blocks, widths) if width <= max_width]


def _sort_frame_rows_in_reading_order(rows: List[FrameRow]) -> List[FrameRow]:
    ordered_rows = sorted(rows, key=lambda item: (item.page, item.top, item.x0))
    if not ordered_rows:
        return []

    grouped: Dict[int, List[FrameRow]] = {}
    for row in ordered_rows:
        grouped.setdefault(row.page, []).append(row)

    result: List[FrameRow] = []
    for page in sorted(grouped):
        page_rows = grouped[page]
        bands: List[Dict[str, float]] = []
        keys: Dict[int, Tuple[int, float, float]] = {}

        for idx, row in enumerate(page_rows):
            band_index = -1
            for candidate_index, band in enumerate(bands):
                if abs(row.top - band["y"]) <= READING_ORDER_Y_BAND:
                    band_index = candidate_index
                    count = band["count"]
                    band["y"] = (band["y"] * count + row.top) / (count + 1.0)
                    band["count"] = count + 1.0
                    break

            if band_index == -1:
                bands.append({"y": row.top, "count": 1.0})
                band_index = len(bands) - 1

            keys[idx] = (band_index, row.x0, row.top)

        result.extend(
            row
            for idx, row in sorted(
                enumerate(page_rows),
                key=lambda item: keys[item[0]],
            )
        )
    return result


def _refine_titles_for_reference_rows(rows: List[FrameRow]) -> None:
    for row in rows:
        row.title = _normalize_title(row.title)

    for row in rows:
        note_text = "".join(value for _, value in row.pairs)
        if "取付参考例" in row.title:
            row.code = ""
            row.pairs = []
            continue
        if "取付" not in note_text:
            continue

        sibling_candidates = [
            candidate
            for candidate in rows
            if candidate.page == row.page
            and candidate.title.startswith("マグネットセンサー")
            and abs(candidate.top - row.top) <= 220.0
            and candidate.x0 < row.x0
        ]
        should_promote = (
            row.title == "マグネットセンサー"
            or "通線孔" in row.title
            or row.title.endswith("ボックス")
        )
        if should_promote and sibling_candidates:
            sibling = min(sibling_candidates, key=lambda item: abs(item.x0 - row.x0))
            row.title = f"{sibling.title}取付参考例"
        elif should_promote and row.title == "マグネットセンサー":
            row.title = "マグネットセンサー取付参考例"
        if "取付参考例" in row.title:
            row.code = ""
            row.pairs = []


def build_frame_rows_from_segments(
    segments: List[Segment],
    *,
    title_segments: Optional[List[Segment]] = None,
) -> List[FrameRow]:
    table_segments = [segment for segment in segments if _is_table_segment(segment)]
    blocks = _cluster_table_segments(table_segments)
    _attach_continuation_segments_to_blocks(blocks, segments)
    title_source = title_segments if title_segments is not None else segments
    all_title_candidates = [segment for segment in title_source if _is_title_candidate(segment)]
    code_segments = [segment for segment in segments if _find_code_in_segment(segment)]
    code_row_centers = _header_row_centers_from_codes(code_segments)
    title_candidates = _filter_title_candidates_by_header_rows(all_title_candidates, code_row_centers)
    parsed_blocks: List[ParsedTableBlock] = []
    for block in blocks:
        pairs, label_count = _extract_pairs_from_block(block)
        if label_count >= TABLE_MIN_LABEL_COUNT:
            parsed_blocks.append(ParsedTableBlock(block=block, pairs=pairs, label_count=label_count))
    parsed_blocks = _filter_extreme_wide_blocks(parsed_blocks)

    frame_rows: List[FrameRow] = []
    used_titles: set[Tuple[int, float, float, str]] = set()
    title_segment_by_block: Dict[Tuple[int, int, int, int], Segment] = {}
    split_title_by_block: Dict[Tuple[int, int, int, int], str] = {}

    assignments: Dict[Segment, List[TableBlock]] = {}
    for parsed_block in sorted(parsed_blocks, key=lambda item: (item.block.page, item.block.top, item.block.x0)):
        block = parsed_block.block
        title_segment = _pick_title_for_block(block, title_candidates, min_overlap=0.15)
        if title_segment is None:
            title_segment = _pick_title_for_block(block, all_title_candidates, min_overlap=0.05)
        if title_segment is None:
            continue
        title_segment_by_block[_block_key(block)] = title_segment
        assignments.setdefault(title_segment, []).append(block)

    for title_segment, assigned_blocks in assignments.items():
        if len(assigned_blocks) == 1:
            block = assigned_blocks[0]
            split_title_by_block[_block_key(block)] = _resolve_title_text_for_block(title_segment, block)
            continue
        split_title_by_block.update(_split_title_text_by_blocks(title_segment, assigned_blocks))

    for parsed_block in sorted(parsed_blocks, key=lambda item: (item.block.page, item.block.top, item.block.x0)):
        block = parsed_block.block
        block_key = _block_key(block)
        title_segment = title_segment_by_block.get(block_key)
        title = split_title_by_block.get(block_key, "")
        if title_segment is not None:
            used_titles.add((title_segment.page, title_segment.row_y, title_segment.x0, title_segment.text_compact))

        if not title:
            header_y_estimate = _estimate_header_y_for_block(block, code_row_centers)
            nearby_header_titles = [
                segment
                for segment in all_title_candidates
                if segment.page == block.page
                and segment.row_y < block.top
                and abs(segment.row_y - header_y_estimate) <= 150.0
            ]
            fallback_pool = nearby_header_titles if nearby_header_titles else all_title_candidates
            fallback_segment = _pick_title_for_block(block, fallback_pool, min_overlap=0.02)
            if fallback_segment is not None:
                title = _resolve_title_text_for_block(fallback_segment, block)
                title_segment = fallback_segment
                used_titles.add((fallback_segment.page, fallback_segment.row_y, fallback_segment.x0, fallback_segment.text_compact))

        code = (
            _pick_code_for_title(block=block, header_y=title_segment.row_y, code_segments=code_segments)
            if title_segment
            else ""
        )
        if not code and title_segment is not None:
            code = _pick_code_for_anchor(
                page=block.page,
                anchor_x0=block.x0,
                anchor_x1=block.x1,
                anchor_y=title_segment.row_y,
                max_y=title_segment.row_y + 220.0,
                code_segments=code_segments,
                x_pad_left=80.0,
                x_pad_right=120.0,
                min_overlap=0.35,
            )

        if not title:
            continue

        frame_rows.append(
            FrameRow(
                page=block.page,
                top=block.top,
                x0=block.x0,
                title=title,
                code=code,
                pairs=parsed_block.pairs,
            )
        )

    # Avoid unrelated large-frame pickup when table-based frames are detected.
    if not frame_rows:
        for segment in title_candidates:
            title_key = (segment.page, segment.row_y, segment.x0, segment.text_compact)
            if title_key in used_titles:
                continue

            code = _pick_code_for_anchor(
                page=segment.page,
                anchor_x0=segment.x0,
                anchor_x1=segment.x1,
                anchor_y=segment.row_y,
                max_y=segment.row_y + 260.0,
                code_segments=code_segments,
            )

            if not code:
                has_only_one_title = len(title_candidates) == 1
                has_no_blocks = len(blocks) == 0
                has_no_codes = len(code_segments) == 0
                if not (has_only_one_title and has_no_blocks and has_no_codes):
                    continue

            frame_rows.append(
                FrameRow(
                    page=segment.page,
                    top=segment.top,
                    x0=segment.x0,
                    title=_normalize_title(segment.text_compact),
                    code=code,
                    pairs=[],
                )
            )

    normalized_rows: List[FrameRow] = []
    for row in frame_rows:
        if row.title and row.title.startswith("[") and row.title.endswith("]"):
            row.title = row.title.strip("[]")
        values = row.values
        if not values:
            continue
        normalized_rows.append(row)

    _refine_titles_for_reference_rows(normalized_rows)

    deduped: List[FrameRow] = []
    seen_signatures: set[Tuple[int, Tuple[str, ...]]] = set()
    for row in _sort_frame_rows_in_reading_order(normalized_rows):
        signature = (row.page, tuple(row.values))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(row)
    return deduped


def write_e142_csv(rows: List[List[str]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def extract_e142_pdf(
    pdf_path: Path,
    out_csv: Path,
    debug_dir: Path,
    vision_service_account_json: str,
    page: int = 0,
    dpi: int = 300,
    y_cluster: float = 12.0,
    x_gap: float = 70.0,
) -> Dict[str, object]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"入力PDFが見つかりません: {pdf_path}")

    client = build_vision_client(vision_service_account_json)
    total_pages = count_pdf_pages(pdf_path)
    target_pages = resolve_target_pages(total_pages=total_pages, page=page)

    all_rows: List[FrameRow] = []
    rows_by_page: Dict[int, int] = {}

    with TemporaryDirectory() as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        for target_page in target_pages:
            png_path = run_pdftoppm(pdf_path=pdf_path, page=target_page, dpi=dpi, work_dir=tmp_dir)
            with Image.open(png_path) as source_image:
                page_image = source_image.convert("RGB")
            try:
                words = extract_words(client, page_image)
                segments = build_segments_from_words(
                    words,
                    page=target_page,
                    y_cluster=y_cluster,
                    x_gap=x_gap,
                )
                title_only_segments = build_segments_from_words(
                    words,
                    page=target_page,
                    y_cluster=y_cluster,
                    x_gap=TITLE_SEGMENT_X_GAP,
                )
                page_rows = build_frame_rows_from_segments(
                    segments,
                    title_segments=title_only_segments,
                )
                rows_by_page[target_page] = len(page_rows)
                all_rows.extend(page_rows)
            finally:
                page_image.close()

    all_rows = _sort_frame_rows_in_reading_order(all_rows)
    csv_rows = [row.values for row in all_rows]
    write_e142_csv(csv_rows, out_csv)

    max_columns = max((len(row) for row in csv_rows), default=0)
    return {
        "rows": len(csv_rows),
        "columns": [f"column_{index + 1}" for index in range(max_columns)],
        "output_csv": str(out_csv),
        "pages_processed": len(target_pages),
        "target_pages": target_pages,
        "rows_by_page": rows_by_page,
        "debug_dir": str(debug_dir),
    }
