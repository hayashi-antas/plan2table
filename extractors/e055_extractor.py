from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Tuple

from PIL import Image

from extractors.raster_extractor import (
    build_vision_client,
    count_pdf_pages,
    resolve_target_pages,
    run_pdftoppm,
    vision,
)

OUTPUT_COLUMNS = ["機器器具", "メーカー", "型番"]
MODEL_PATTERN = re.compile(r"\b([A-Z]{2,}(?:\s*-\s*[A-Z0-9]{1,20})+)\b")
MODEL_MULTIPLIER_SUFFIX_PATTERN = re.compile(r"\s*(?:\(\s*[xX×✕]\s*\d+\s*\)|[xX×✕]\s*\d+)")  # noqa: RUF001
COLON_MODEL_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9&._-]{1,30})\s*[:：]\s*([A-Z]{2,}(?:\s*-\s*[A-Z0-9]{1,20})+)")  # noqa: RUF001
EXCLUDED_EMERGENCY_CODES = {"EDL", "EDM", "ECL", "ECM", "ECH", "ES1", "ES2"}  # noqa: RUF001


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
            writer.writerow(row)


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
    if upper in {"EDL", "ECL", "EDM", "ECM", "ECH"}:
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


def _extract_model_without_colon(segment_text: str) -> str:
    text = _cleanup_model_text(segment_text)
    hyphen_model = MODEL_PATTERN.search(text)
    if hyphen_model:
        return _append_multiplier_suffix(text, hyphen_model.group(1), hyphen_model.end(1))
    return ""


def _normalize_doujou_model(segment_text: str) -> str:
    compact = compact_text(segment_text).lower()
    if "同上" not in compact:
        return ""
    if re.search(r"(ガ[ー-]?ド|犬[-ー]?f|一卡付|卡付|カード)", compact):
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


def _extract_model_only_candidates(words: List[WordBox]) -> List[Dict[str, object]]:
    sorted_words = sorted(words, key=lambda item: item.cx)
    if len(sorted_words) < 2:
        return []

    tokens = [normalize_text(word.text).strip() for word in sorted_words]
    row_text = " ".join(tokens)
    if not re.search(r"\d+(?:\.\d+)?\s*W", row_text, flags=re.IGNORECASE):
        return []
    candidates: List[Dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for match in MODEL_PATTERN.finditer(row_text):
        model = _append_multiplier_suffix(row_text, match.group(1), match.end(1))
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
    candidates: List[Dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for match in COLON_MODEL_PATTERN.finditer(row_text):
        maker = match.group(1).strip()
        model = _append_multiplier_suffix(row_text, match.group(2), match.end(2))
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
                row["model_x"] = source.get("model_x", source.get("row_x", row.get("model_x", row.get("row_x", 0.0))))
        else:
            available_sources = list(source_rows)
            for row in current_rows:
                row_model_x = float(row.get("model_x", row.get("row_x", 0.0)))
                source_pool = available_sources or source_rows
                source = min(
                    source_pool,
                    key=lambda source_row: abs(
                        float(source_row.get("model_x", source_row.get("row_x", 0.0))) - row_model_x
                    ),
                )
                row["機器器具"] = source.get("機器器具", "")
                row["block_index"] = source.get("block_index", row.get("block_index", 0))
                row["model_x"] = source.get("model_x", source.get("row_x", row.get("model_x", row.get("row_x", 0.0))))
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
        has_colon_token = any(":" in token or "：" in token for token in tokens)
        if has_colon_token:
            colon_candidates = _extract_colon_model_only_candidates(words)
            if colon_candidates:
                return colon_candidates
        model_only_candidates = _extract_model_only_candidates(words)
        if model_only_candidates:
            return model_only_candidates
        return _extract_colon_model_only_candidates(words)

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
                if maker_start >= 0:
                    maker_token_index = _char_pos_to_token_index(segment_tokens, maker_start)
                    model_x = round(float(words[code_start + maker_token_index].bbox[0]), 2)
            elif model:
                equivalent_model = model
        else:
            remainder = " ".join(segment_tokens[1:])
            equivalent_model = _normalize_doujou_model(remainder)
            if not equivalent_model:
                equivalent_model = _extract_model_without_colon(remainder)

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


def _extract_page_candidate_rows(
    *,
    client: vision.ImageAnnotatorClient,
    page_image: Image.Image,
    page_number: int,
    y_cluster: float,
) -> List[Dict[str, object]]:
    words = _extract_words(client, page_image)
    clusters = _cluster_by_y(words, y_cluster)
    header_indexes = [idx for idx, cluster in enumerate(clusters) if _is_header_row(_row_text(cluster))]
    if not header_indexes:
        return []

    candidates: List[Dict[str, object]] = []
    for header_pos, header_index in enumerate(header_indexes):
        next_header_index = header_indexes[header_pos + 1] if header_pos + 1 < len(header_indexes) else len(clusters)
        section_candidates: List[Dict[str, object]] = []
        for cluster in clusters[header_index + 1:next_header_index]:
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
        for row in section_candidates:
            if not x_centers:
                row["block_index"] = 0
            else:
                x = float(row["row_x"])
                row["block_index"] = min(range(len(x_centers)), key=lambda idx: abs(x - x_centers[idx]))
        _propagate_equipment_in_section(section_candidates)
        for row in section_candidates:
            candidates.append(row)
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
    del debug_dir  # reserved for future debug image output
    if not pdf_path.exists():
        raise FileNotFoundError(f"入力PDFが見つかりません: {pdf_path}")

    client = build_vision_client(vision_service_account_json)
    total_pages = count_pdf_pages(pdf_path)
    target_pages = resolve_target_pages(total_pages=total_pages, page=page)

    candidate_rows: List[Dict[str, object]] = []
    rows_by_page: Dict[int, int] = {}
    with TemporaryDirectory() as tmp_dir_raw:
        tmp_dir = Path(tmp_dir_raw)
        for target_page in target_pages:
            png_path = run_pdftoppm(pdf_path, target_page, dpi, tmp_dir)
            page_image = Image.open(png_path).convert("RGB")
            page_candidates = _extract_page_candidate_rows(
                client=client,
                page_image=page_image,
                page_number=target_page,
                y_cluster=y_cluster,
            )
            rows_by_page[target_page] = len(page_candidates)
            candidate_rows.extend(page_candidates)

    rows = build_output_rows(candidate_rows)
    write_csv(rows, out_csv)
    return {
        "rows": len(rows),
        "columns": OUTPUT_COLUMNS,
        "output_csv": str(out_csv),
        "pages_processed": len(target_pages),
        "target_pages": target_pages,
        "rows_by_page": rows_by_page,
    }
