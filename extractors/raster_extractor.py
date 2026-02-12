#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

try:
    from google.cloud import vision
    from google.oauth2 import service_account
    _VISION_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on local env
    vision = None
    service_account = None
    _VISION_IMPORT_ERROR = exc
from PIL import Image, ImageDraw


OUTPUT_COLUMNS = ["機器番号", "機器名称", "電圧(V)", "容量(kW)"]

SIDE_SPLITS = {
    "L": (0.0, 0.0, 0.5, 1.0),
    "R": (0.5, 0.0, 1.0, 1.0),
}

DEFAULT_CENTER_RATIOS = [0.24, 0.35, 0.40, 0.44]
HEADER_Y_CLUSTER = 22.0
DATA_START_OFFSET = 140.0

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google Cloud Vision APIでPDF表から4列を抽出する"
    )
    parser.add_argument("--pdf", default="/data/電気図1.pdf", help="入力PDFパス")
    parser.add_argument("--page", type=int, default=1, help="1始まりのページ番号")
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


def run_pdftoppm(pdf_path: Path, page: int, dpi: int, work_dir: Path) -> Path:
    if page < 1:
        raise ValueError("--page は1以上を指定してください。")
    png_base = work_dir / "page"
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

    return build_bounds_from_centers(
        [c1f, c2f, c3f, c4f],
        header_y=best.row_y,
        side_width=side_width,
    )


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
        return OUTPUT_COLUMNS[0]
    if x < bounds.b23:
        return OUTPUT_COLUMNS[1]
    if x < bounds.b34:
        return OUTPUT_COLUMNS[2]
    return OUTPUT_COLUMNS[3]


def clean_cell(text: str) -> str:
    text = normalize_text(text)
    text = text.strip().replace(" ", "")
    return text.strip("|,:;[]()")


def contains_japanese(text: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-龥]", text))


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
    if name == "湧水ポンプ":
        name = "清水ポンプ"

    volt_norm = normalize_text(volt)
    if volt_norm == "1/200":
        volt = "1φ200"

    power_norm = normalize_text(power)
    if re.fullmatch(r"\d+\.0+", power_norm):
        power = str(int(float(power_norm)))

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

    if not combined:
        return False
    if is_header_row(combined):
        return False
    if any(kw in combined for kw in ["盤姿図", "主開閉器", "トリップ", "ロック連動"]):
        return False

    if re.search(r"[A-Z]{1,4}-[A-Z0-9]{1,6}", code):
        return True
    if any(k in name for k in ROW_FILTER_NAME_KEYWORDS):
        return True
    if "同上用フロートスイッチ" in name or "操作電源" in name:
        return True
    if name and re.search(r"\d", volt):
        return True
    if name and re.search(r"\d", power):
        return True

    return False


def rows_from_words(
    words: List[WordBox], bounds: ColumnBounds, y_cluster: float
) -> List[Dict[str, object]]:
    start_y = bounds.header_y + DATA_START_OFFSET
    target_words = [w for w in words if w.cy >= start_y]
    clusters = cluster_by_y(target_words, y_cluster)

    rows: List[Dict[str, object]] = []
    row_idx = 1
    for cluster in clusters:
        cols: Dict[str, List[WordBox]] = {c: [] for c in OUTPUT_COLUMNS}
        for w in cluster.words:
            col = assign_column(w.cx, bounds)
            if col is not None:
                cols[col].append(w)

        if all(not cols[c] for c in OUTPUT_COLUMNS):
            continue

        row = {
            "機器番号": clean_cell("".join(w.text for w in sorted(cols["機器番号"], key=lambda x: x.cx))),
            "機器名称": clean_cell("".join(w.text for w in sorted(cols["機器名称"], key=lambda x: x.cx))),
            "電圧(V)": clean_cell("".join(w.text for w in sorted(cols["電圧(V)"], key=lambda x: x.cx))),
            "容量(kW)": clean_cell("".join(w.text for w in sorted(cols["容量(kW)"], key=lambda x: x.cx))),
        }
        row = normalize_row_cells(row)

        normalized = normalize_text("".join(row.values()))
        if is_footer_row(normalized):
            break
        if is_header_row(normalized):
            continue
        if not is_data_row(row):
            continue

        rows.append(
            {
                "row_index": row_idx,
                "row_y": round(cluster.row_y, 2),
                **row,
            }
        )
        row_idx += 1

    return rows


def save_debug_image(
    side_image: Image.Image,
    words: List[WordBox],
    bounds: ColumnBounds,
    out_path: Path,
) -> None:
    debug = side_image.convert("RGB")
    draw = ImageDraw.Draw(debug)

    for x in [bounds.x_min, bounds.b12, bounds.b23, bounds.b34, bounds.x_max]:
        draw.line([(x, 0), (x, debug.height)], fill=(255, 120, 0), width=2)
    draw.line(
        [(0, bounds.header_y + DATA_START_OFFSET), (debug.width, bounds.header_y + DATA_START_OFFSET)],
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

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        png_path = run_pdftoppm(pdf_path, page, dpi, tmp_dir)
        page_image = Image.open(png_path).convert("RGB")
        sides = split_sides(page_image)

        for side in ["L", "R"]:
            side_image = sides[side]
            words = extract_words(client, side_image)
            if not words:
                continue
            bounds = infer_column_bounds(words, side_image.width)
            rows = rows_from_words(words, bounds, y_cluster)
            for row in rows:
                row["side"] = side
                all_rows.append(row)
            save_debug_image(
                side_image,
                words,
                bounds,
                debug_dir / f"bbox_{side}.png",
            )

    all_rows.sort(key=lambda r: (str(r["side"]), int(r["row_index"])))
    write_csv(all_rows, out_csv)
    return {
        "rows": len(all_rows),
        "columns": OUTPUT_COLUMNS,
        "output_csv": str(out_csv),
        "debug_dir": str(debug_dir),
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
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        png_path = run_pdftoppm(pdf_path, args.page, args.dpi, tmp_dir)
        page_image = Image.open(png_path).convert("RGB")
        sides = split_sides(page_image)
        for side in ["L", "R"]:
            side_image = sides[side]
            words = extract_words(client, side_image)
            if not words:
                continue
            bounds = infer_column_bounds(words, side_image.width)
            rows = rows_from_words(words, bounds, y_cluster)
            for row in rows:
                row["side"] = side
                all_rows.append(row)
            save_debug_image(side_image, words, bounds, debug_dir / f"bbox_{side}.png")
    all_rows.sort(key=lambda r: (str(r["side"]), int(r["row_index"])))
    write_csv(all_rows, out_csv)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
