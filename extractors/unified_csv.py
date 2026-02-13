from __future__ import annotations

import csv
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

COLUMN_ALIASES: Dict[str, List[str]] = {
    "equipment_id": ["機器番号", "機械番号"],
    "vector_name": ["名称", "機器名称"],
    "vector_power_per_unit_kw": [
        "動力 (50Hz)_消費電力 (KW)",
        "動力(50Hz)_消費電力(KW)",
        "動力(50Hz)_消費電力(Kw)",
        "動力 (50Hz)_消費電力 (Kw)",
    ],
    "vector_count": ["台数"],
    "raster_name": ["機器名称", "名称"],
    "raster_voltage": ["電圧(V)", "電圧（V）"],
    "raster_capacity_kw": ["容量(kW)", "容量(KW)", "容量(Kw)", "容量（kW）"],
}

OUTPUT_COLUMNS = [
    "照合結果",
    "不一致内容",
    "機器ID",
    "機器名",
    "機器表 台数",
    "盤表 台数",
    "台数差（盤表-機器表）",
    "機器表 消費電力(kW)",
    "盤表 容量(kW)",
    "容量差(kW)",
]

EPS_KW = 0.1


def _normalize_header(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.lower()


def _normalize_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.upper()


def _resolve_header(fieldnames: Iterable[str], canonical_key: str) -> Optional[str]:
    normalized_to_header = {_normalize_header(name): name for name in fieldnames}
    for alias in COLUMN_ALIASES[canonical_key]:
        matched = normalized_to_header.get(_normalize_header(alias))
        if matched:
            return matched
    return None


def _parse_number(value: str) -> Optional[float]:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if text in {"", "-", "－", "—"}:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.12g}"


def _collect_capacity_variants(values: Iterable[str]) -> List[Tuple[str, Optional[float]]]:
    seen = set()
    variants: List[Tuple[str, Optional[float]]] = []
    for raw in values:
        text = unicodedata.normalize("NFKC", str(raw or "")).strip()
        if text in {"", "-", "－", "—"}:
            continue
        parsed = _parse_number(text)
        display = _format_number(parsed) if parsed is not None else text
        if not display or display in seen:
            continue
        seen.add(display)
        variants.append((display, parsed))
    return variants


def _pick_capacity_variant(
    variants: List[Tuple[str, Optional[float]]], index: int
) -> Tuple[str, Optional[float]]:
    if not variants:
        return "", None
    if len(variants) == 1:
        return variants[0]
    if index < len(variants):
        return variants[index]
    return "", None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        rows = [dict(row) for row in reader]
    return list(reader.fieldnames), rows


def merge_vector_raster_csv(
    vector_csv_path: Path,
    raster_csv_path: Path,
    out_csv_path: Path,
) -> Dict[str, object]:
    vector_headers, vector_rows = _read_csv(vector_csv_path)
    raster_headers, raster_rows = _read_csv(raster_csv_path)

    vector_id_header = _resolve_header(vector_headers, "equipment_id")
    vector_name_header = _resolve_header(vector_headers, "vector_name")
    vector_power_header = _resolve_header(vector_headers, "vector_power_per_unit_kw")
    vector_count_header = _resolve_header(vector_headers, "vector_count")
    if not vector_id_header or not vector_power_header or not vector_count_header:
        raise ValueError("Vector CSV required headers are missing.")

    raster_id_header = _resolve_header(raster_headers, "equipment_id")
    raster_name_header = _resolve_header(raster_headers, "raster_name")
    raster_voltage_header = _resolve_header(raster_headers, "raster_voltage")
    raster_capacity_header = _resolve_header(raster_headers, "raster_capacity_kw")
    if (
        not raster_id_header
        or not raster_name_header
        or not raster_voltage_header
        or not raster_capacity_header
    ):
        raise ValueError("Raster CSV required headers are missing.")

    raster_agg: Dict[str, Dict[str, object]] = {}
    for row in raster_rows:
        key = _normalize_key(row.get(raster_id_header, ""))
        if not key:
            continue

        agg = raster_agg.get(key)
        if agg is None:
            agg = {
                "names": [],
                "voltages": [],
                "capacity_values": [],
                "match_count": 0,
            }
            raster_agg[key] = agg

        agg["match_count"] = int(agg["match_count"]) + 1
        agg["names"].append(row.get(raster_name_header, ""))  # type: ignore[index]
        agg["voltages"].append(row.get(raster_voltage_header, ""))  # type: ignore[index]
        capacity_raw = row.get(raster_capacity_header, "")
        agg["capacity_values"].append(capacity_raw)  # type: ignore[index]

    out_rows: List[Dict[str, str]] = []
    for vector_row in vector_rows:
        equipment_id = vector_row.get(vector_id_header, "")
        key = _normalize_key(equipment_id)
        agg = raster_agg.get(key)

        power_per_unit_raw = vector_row.get(vector_power_header, "")
        vector_count = _parse_number(vector_row.get(vector_count_header, ""))
        vector_name = vector_row.get(vector_name_header, "") if vector_name_header else ""

        raster_match_count = 0
        raster_capacity_variants: List[Tuple[str, Optional[float]]] = []
        if agg:
            raster_match_count = int(agg["match_count"])
            raster_capacity_variants = _collect_capacity_variants(agg["capacity_values"])  # type: ignore[arg-type]

        vector_capacity_variants = _collect_capacity_variants([power_per_unit_raw])

        count_diff: Optional[float] = None
        if vector_count is not None:
            count_diff = float(raster_match_count) - vector_count

        exists_ok = raster_match_count > 0
        qty_ok = count_diff is not None and count_diff == 0.0

        line_count = max(1, len(vector_capacity_variants), len(raster_capacity_variants))
        for line_index in range(line_count):
            vector_power_display, vector_power_value = _pick_capacity_variant(
                vector_capacity_variants, line_index
            )
            raster_capacity_display, raster_capacity_value = _pick_capacity_variant(
                raster_capacity_variants, line_index
            )

            capacity_diff: Optional[float] = None
            if raster_capacity_value is not None and vector_power_value is not None:
                capacity_diff = raster_capacity_value - vector_power_value

            kw_ok = capacity_diff is not None and abs(capacity_diff) <= EPS_KW
            overall_ok = exists_ok and qty_ok and kw_ok

            mismatch_reason = ""
            if not overall_ok:
                if not exists_ok:
                    mismatch_reason = "盤表に記載なし"
                elif not qty_ok:
                    if count_diff is None:
                        mismatch_reason = "台数差分=欠損"
                    else:
                        mismatch_reason = f"台数差分={_format_number(count_diff)}"
                elif capacity_diff is None:
                    mismatch_reason = "容量欠損"
                else:
                    mismatch_reason = f"容量差分={capacity_diff:.3f}"

            out_row = {
                "照合結果": "一致" if overall_ok else "不一致",
                "不一致内容": mismatch_reason,
                "機器ID": equipment_id,
                "機器名": vector_name,
                "機器表 台数": _format_number(vector_count),
                "盤表 台数": str(raster_match_count),
                "台数差（盤表-機器表）": _format_number(count_diff),
                "機器表 消費電力(kW)": vector_power_display,
                "盤表 容量(kW)": raster_capacity_display,
                "容量差(kW)": _format_number(capacity_diff),
            }
            if line_index > 0:
                out_row["照合結果"] = ""
                out_row["不一致内容"] = ""
                out_row["機器表 台数"] = ""
                out_row["盤表 台数"] = ""
                out_row["台数差（盤表-機器表）"] = ""
            out_rows.append(out_row)

    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    out_columns = OUTPUT_COLUMNS
    with out_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)

    return {
        "rows": len(out_rows),
        "columns": out_columns,
        "output_csv": str(out_csv_path),
        "vector_csv": str(vector_csv_path),
        "raster_csv": str(raster_csv_path),
    }
