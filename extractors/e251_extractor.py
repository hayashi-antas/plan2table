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

OUTPUT_COLUMNS = ["器具記号", "メーカー", "相当型番"]

DASH_VARIANTS_PATTERN = re.compile(r"[ー―−–—‐ｰ－]")  # noqa: RUF001
EQUIPMENT_CODE_PATTERN = re.compile(r"^[A-Z]\d{1,2}$")
EQUIPMENT_LABEL_PATTERN = re.compile(r"^(?P<code>[A-Z]\d{1,2})(?:\((?P<suffix>[^()]+)\))?$")
WATTAGE_ONLY_MODEL_PATTERN = re.compile(r"^\d+(?:\.\d+)?W$", flags=re.IGNORECASE)
DASH_TOKEN_CLASS = r"[-‐‑‒–—―ー−－]"  # noqa: RUF001
MODEL_TOKEN_PATTERN = (
    r"(?:"
    + rf"[A-Z0-9]+(?:\s*{DASH_TOKEN_CLASS}\s*[A-Z0-9]+)+"
    + r"|"
    + r"[A-Z]{2,}[A-Z0-9]{3,}"
    + r")"
)

EQ_COLON_MAKER_MODEL_PATTERN = re.compile(
    r"\b(?P<eq>[A-Z]\d{1,2})\s*(?P<eq_suffix>\([^)]+\))?\s*[:：]\s*"
    r"(?P<maker>[A-Za-z][A-Za-z0-9&._-]{1,30})\s+"
    + rf"(?P<model>{MODEL_TOKEN_PATTERN})"
)
MAKER_COLON_MODEL_PATTERN = re.compile(
    r"\b(?P<maker>[A-Za-z][A-Za-z0-9&._-]{1,30})\s*[:：]\s*"
    + rf"(?P<model>{MODEL_TOKEN_PATTERN})"
)
MAKER_SPACE_MODEL_PATTERN = re.compile(
    r"\b(?P<maker>[A-Za-z][A-Za-z0-9&._-]{1,30})\s+"
    + rf"(?P<model>{MODEL_TOKEN_PATTERN})"
)


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
class EquipmentAnchor:
    x: float
    raw: str
    equipment: str


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "")


def compact_text(value: str) -> str:
    return normalize_text(value).replace(" ", "").replace("　", "")


def _normalize_dash(value: str) -> str:
    return DASH_VARIANTS_PATTERN.sub("-", normalize_text(value))


def _normalize_token(value: str) -> str:
    token = _normalize_dash(value).upper()
    token = token.strip("[](){}<>|,.;:'\"")
    return token


def _is_equipment_code(value: str) -> bool:
    return bool(EQUIPMENT_CODE_PATTERN.fullmatch(_normalize_token(value)))


def _normalize_equipment_label(value: str) -> str:
    text = _normalize_dash(normalize_text(value)).upper()
    text = re.sub(r"\s+", "", text)
    matched = EQUIPMENT_LABEL_PATTERN.fullmatch(text)
    if not matched:
        return ""
    code = matched.group("code")
    suffix = (matched.group("suffix") or "").strip()
    if suffix:
        return f"{code}({suffix})"
    return code


def _is_symbol_like(value: str) -> bool:
    token = _normalize_token(value)
    return bool(re.fullmatch(r"[A-Z]", token))


def _cleanup_model(value: str) -> str:
    text = _normalize_dash(value)
    text = re.sub(r"\s*-\s*", "-", text)
    text = text.strip(" |[](){}<>,.;")
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _is_likely_maker(value: str) -> bool:
    maker = normalize_text(value).strip()
    if not maker:
        return False
    if _is_equipment_code(maker):
        return False
    return len(maker) >= 3


def _is_likely_model(value: str) -> bool:
    model = _cleanup_model(value).upper()
    if len(model) < 4:
        return False
    if not re.search(r"\d", model):
        return False
    if re.fullmatch(r"(?:PF|VVF|SCV)[0-9A-Z.-]*", model):
        return False
    if WATTAGE_ONLY_MODEL_PATTERN.fullmatch(model):
        return False
    if re.fullmatch(r"LED\d+(?:\.\d+)?W", model, flags=re.IGNORECASE):
        return False
    return True


def write_csv(rows: List[Dict[str, str]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "器具記号": str(row.get("器具記号", "") or ""),
                    "メーカー": str(row.get("メーカー", "") or ""),
                    "相当型番": str(row.get("相当型番", "") or ""),
                }
            )


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


def _is_section_title(value: str) -> bool:
    compact = compact_text(value)
    return "住戸内" in compact and "照明器具姿図" in compact


def _char_pos_to_token_index(tokens: List[str], char_pos: int) -> int:
    cursor = 0
    for idx, token in enumerate(tokens):
        next_cursor = cursor + len(token)
        if cursor <= char_pos < next_cursor:
            return idx
        cursor = next_cursor + 1
    return max(len(tokens) - 1, 0)


def _extract_section_words(words: List[WordBox], y_cluster: float = 14.0) -> Tuple[List[WordBox], float]:
    clusters = _cluster_by_y(words, y_cluster)
    title_cluster = next((cluster for cluster in clusters if _is_section_title(_row_text(cluster))), None)
    if title_cluster is None:
        return [], 0.0

    x_min = min(word.bbox[0] for word in title_cluster.words) - 20.0
    y_min = title_cluster.row_y - 20.0
    y_max = title_cluster.row_y + 520.0
    section_words = [
        word
        for word in words
        if word.bbox[0] >= x_min and y_min <= word.cy <= y_max
    ]
    return section_words, float(title_cluster.row_y)


def _detect_anchors(clusters: List[RowCluster], *, title_y: float) -> List[EquipmentAnchor]:
    anchors: List[EquipmentAnchor] = []
    seen: set[tuple[str, int]] = set()

    for cluster in clusters:
        if cluster.row_y < title_y or cluster.row_y > title_y + 120.0:
            continue
        words = sorted(cluster.words, key=lambda item: item.cx)
        idx = 0
        while idx < len(words):
            token = _normalize_token(words[idx].text)
            raw = token
            x = float(words[idx].bbox[0])

            # OCR may split a code like "D1" into two tokens: "D" and "1".
            if re.fullmatch(r"[A-Z]", token) and idx + 1 < len(words):
                next_token = _normalize_token(words[idx + 1].text)
                if re.fullmatch(r"\d{1,2}", next_token):
                    gap = float(words[idx + 1].bbox[0] - words[idx].bbox[2])
                    if gap <= 20.0:
                        raw = f"{token}{next_token}"
                        idx += 1

            if _is_equipment_code(raw):
                equipment = raw
            elif _is_symbol_like(raw):
                equipment = ""
            else:
                idx += 1
                continue

            dedupe_key = (raw, int(round(x)))
            if dedupe_key not in seen:
                anchors.append(EquipmentAnchor(x=x, raw=raw, equipment=equipment))
                seen.add(dedupe_key)
            idx += 1

    anchors.sort(key=lambda item: item.x)
    return anchors


def _extract_candidates_from_cluster(cluster: RowCluster) -> List[Dict[str, object]]:
    words = sorted(cluster.words, key=lambda item: item.cx)
    if len(words) < 2:
        return []

    tokens = [normalize_text(word.text).strip() for word in words]
    row_text = _normalize_dash(" ".join(tokens))
    compact = compact_text(row_text)

    if "型番は相当品とする" in compact or compact.startswith("注記"):
        return []

    candidates: List[Dict[str, object]] = []
    seen: set[tuple[str, str, str, float]] = set()
    occupied_spans: List[Tuple[int, int]] = []

    for match in EQ_COLON_MAKER_MODEL_PATTERN.finditer(row_text):
        equipment = _normalize_equipment_label(
            f"{match.group('eq').strip()}{match.group('eq_suffix') or ''}"
        )
        maker = match.group("maker").strip()
        model = _cleanup_model(match.group("model").upper())
        if not equipment or not _is_likely_maker(maker) or not _is_likely_model(model):
            continue
        token_index = _char_pos_to_token_index(tokens, match.start("eq"))
        row_x = round(float(words[token_index].bbox[0]), 2)
        candidate_key = (equipment, maker, model, row_x)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        occupied_spans.append((match.start(), match.end()))
        candidates.append(
            {
                "器具記号": equipment,
                "メーカー": maker,
                "相当型番": model,
                "row_x": row_x,
            }
        )

    for pattern, key in (
        (MAKER_COLON_MODEL_PATTERN, "maker"),
        (MAKER_SPACE_MODEL_PATTERN, "maker"),
    ):
        for match in pattern.finditer(row_text):
            start = match.start()
            end = match.end()
            if any(start < span_end and end > span_start for span_start, span_end in occupied_spans):
                continue
            maker = match.group("maker").strip()
            model = _cleanup_model(match.group("model").upper())
            if not _is_likely_maker(maker) or not _is_likely_model(model):
                continue
            token_index = _char_pos_to_token_index(tokens, match.start(key))
            row_x = round(float(words[token_index].bbox[0]), 2)
            candidate_key = ("", maker, model, row_x)
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            candidates.append(
                {
                    "器具記号": "",
                    "メーカー": maker,
                    "相当型番": model,
                    "row_x": row_x,
                }
            )

    return candidates


def _assign_equipment_from_anchors(
    candidates: List[Dict[str, object]],
    *,
    anchors: List[EquipmentAnchor],
    max_distance: float = 520.0,
) -> None:
    if not anchors:
        return

    for row in candidates:
        equipment = _normalize_equipment_label(str(row.get("器具記号", "")))
        if equipment:
            row["器具記号"] = equipment
            continue

        row_x = float(row.get("row_x", 0.0))
        nearest = min(anchors, key=lambda anchor: abs(anchor.x - row_x))
        if abs(nearest.x - row_x) > max_distance:
            row["器具記号"] = ""
            continue
        row["器具記号"] = nearest.equipment if _is_equipment_code(nearest.equipment) else ""


def _cluster_x_positions(values: List[float], tolerance: float = 260.0) -> List[float]:
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


def _assign_block_indexes(candidates: List[Dict[str, object]]) -> None:
    if not candidates:
        return
    x_values = [float(row.get("row_x", 0.0)) for row in candidates]
    x_centers = _cluster_x_positions(x_values, tolerance=260.0)
    if not x_centers:
        for row in candidates:
            row["block_index"] = 0
        return
    for row in candidates:
        x = float(row.get("row_x", 0.0))
        row["block_index"] = min(range(len(x_centers)), key=lambda idx: abs(x - x_centers[idx]))


def build_output_rows(candidates: List[Dict[str, object]]) -> List[Dict[str, str]]:
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            int(item.get("page", 0)),
            int(item.get("block_index", 0)),
            float(item.get("row_y", 0.0)),
            float(item.get("row_x", 0.0)),
        ),
    )

    rows: List[Dict[str, str]] = []
    for item in sorted_candidates:
        equipment = _normalize_equipment_label(str(item.get("器具記号", "")))
        maker = normalize_text(str(item.get("メーカー", "")).strip())
        model = _cleanup_model(str(item.get("相当型番", "")).strip())

        if not maker and not model:
            continue

        rows.append(
            {
                "器具記号": equipment,
                "メーカー": maker,
                "相当型番": model,
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
    section_words, title_y = _extract_section_words(words, y_cluster=y_cluster)
    if not section_words:
        return []

    clusters = _cluster_by_y(section_words, y_cluster)
    anchors = _detect_anchors(clusters, title_y=title_y)

    candidates: List[Dict[str, object]] = []
    for cluster in clusters:
        if cluster.row_y <= title_y + 120.0:
            continue
        row_candidates = _extract_candidates_from_cluster(cluster)
        for row in row_candidates:
            candidates.append(
                {
                    "page": page_number,
                    "row_y": round(float(cluster.row_y), 2),
                    **row,
                }
            )

    _assign_equipment_from_anchors(candidates, anchors=anchors)
    _assign_block_indexes(candidates)
    return candidates


def extract_e251_pdf(
    pdf_path: Path,
    out_csv: Path,
    debug_dir: Path,
    vision_service_account_json: str,
    page: int = 0,
    dpi: int = 300,
    y_cluster: float = 14.0,
) -> Dict[str, object]:
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
        "debug_dir": str(debug_dir),
    }
