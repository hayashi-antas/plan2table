from __future__ import annotations

import csv
from dataclasses import dataclass
import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Tuple

JudgmentCode = Literal["match", "mismatch", "review"]

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
    "vector_drawing_number": ["図面番号", "図番", "機器表 図面番号"],
    "raster_name": ["機器名称", "名称"],
    "raster_voltage": ["電圧(V)", "電圧（V）"],
    "raster_capacity_kw": ["容量(kW)", "容量(KW)", "容量(Kw)", "容量（kW）"],
    "raster_drawing_number": ["図面番号", "盤表 図面番号"],
}

OUTPUT_COLUMNS = [
    "総合判定",
    "台数判定",
    "容量判定",
    "名称判定",
    "機器ID照合",
    "判定理由",
    "機器ID",
    "機器表 記載名",
    "盤表 記載名",
    "機器表 台数",
    "盤表 台数",
    "台数差",
    "機器表 消費電力(kW)",
    "機器表 モード容量(kW)",
    "機器表 判定モード",
    "機器表 判定採用容量(kW)",
    "容量判定補足",
    "盤表 容量(kW)",
    "容量差(kW)",
    "機器表 図面番号",
    "盤表 図面番号",
    "盤表 記載トレース",
]

EPS_KW = 0.1
BLANK_TOKENS = {"", "-", "－", "—"}
THOUSANDS_PATTERN = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
MODE_CAPACITY_PATTERN = re.compile(r"\((冷|暖|低温)\)\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)")
CAPACITY_MODE_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("冷", ("冷房専用",)),
    ("暖", ("暖房専用",)),
    ("低温", ("低温専用",)),
)
MODE_ORDER = ("冷", "暖", "低温")
MAX_MODE_TIE_EPS = 1e-9
CAPACITY_FALLBACK_MAX = "max"
CAPACITY_FALLBACK_STRICT = "strict"
CapacityVariant = Tuple[str, Optional[float], str]


def to_mark(code: JudgmentCode) -> str:
    if code == "match":
        return "◯"
    if code == "mismatch":
        return "✗"
    return "要確認"


def _normalize_header(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.lower()


def _normalize_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.upper()


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


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
    text = _normalize_text(value)
    if text in BLANK_TOKENS:
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


def _classify_capacity_text(raw: str) -> Tuple[str, Optional[float], str]:
    text = _normalize_text(raw)
    if text in BLANK_TOKENS:
        return "blank", None, ""

    if "," in text and not THOUSANDS_PATTERN.match(text):
        return "multi", None, text

    parsed = _parse_number(text)
    if parsed is not None:
        return "numeric", parsed, _format_number(parsed)

    return "non_numeric", None, text


def _collect_capacity_variants(values: Iterable[str]) -> List[CapacityVariant]:
    seen = set()
    variants: List[CapacityVariant] = []
    for raw in values:
        kind, parsed, display = _classify_capacity_text(raw)
        if kind == "blank":
            continue
        key = (display, kind)
        if key in seen:
            continue
        seen.add(key)
        variants.append((display, parsed, kind))
    return variants


def _pick_capacity_variant(variants: List[CapacityVariant], index: int) -> CapacityVariant:
    if not variants:
        return "", None, "blank"
    if len(variants) == 1:
        return variants[0]
    if index < len(variants):
        return variants[index]
    return "", None, "blank"


def _join_capacity_variants(variants: List[CapacityVariant]) -> str:
    return ",".join(display for display, _, _ in variants if display)


@dataclass(frozen=True)
class VectorCapacityResolution:
    raw_display: str
    mode_values_display: str
    selected_mode: str
    selected_display: str
    selected_value: Optional[float]
    selected_kind: str
    note: str
    reason_code: str


def _extract_mode_capacity_values(raw: str) -> Dict[str, float]:
    text = _normalize_text(raw)
    values: Dict[str, float] = {}
    for mode, number_text in MODE_CAPACITY_PATTERN.findall(text):
        parsed = _parse_number(number_text)
        if parsed is None:
            continue
        values[mode] = parsed
    return values


def _format_mode_capacity_values(values: Dict[str, float]) -> str:
    if not values:
        return ""
    ordered_modes: List[str] = [mode for mode in MODE_ORDER if mode in values]
    ordered_modes.extend(mode for mode in values.keys() if mode not in MODE_ORDER)
    return ",".join(f"{mode}={_format_number(values[mode])}" for mode in ordered_modes)


def _infer_capacity_mode_from_name(name: str) -> Tuple[Optional[str], str, bool]:
    normalized = _normalize_text(name)
    matches: List[Tuple[str, str]] = []
    for mode, keywords in CAPACITY_MODE_HINTS:
        for keyword in keywords:
            if keyword in normalized:
                matches.append((mode, keyword))
                break
    if len(matches) == 1:
        mode, keyword = matches[0]
        return mode, keyword, False
    if len(matches) >= 2:
        return None, ",".join(keyword for _, keyword in matches), True
    return None, "", False


def _capacity_fallback_mode() -> str:
    mode = os.getenv("ME_CHECK_CAPACITY_FALLBACK", CAPACITY_FALLBACK_MAX).strip().lower()
    if mode == CAPACITY_FALLBACK_STRICT:
        return CAPACITY_FALLBACK_STRICT
    return CAPACITY_FALLBACK_MAX


def _pick_unique_max_mode(mode_values: Dict[str, float]) -> Tuple[Optional[str], Optional[float], List[str]]:
    if not mode_values:
        return None, None, []
    max_value = max(mode_values.values())
    max_modes = [mode for mode, value in mode_values.items() if abs(value - max_value) <= MAX_MODE_TIE_EPS]
    if len(max_modes) == 1:
        return max_modes[0], max_value, max_modes
    return None, max_value, max_modes


def _resolve_vector_capacity(raw: str, vector_name: str) -> VectorCapacityResolution:
    raw_display = _normalize_text(raw)
    variants = _collect_capacity_variants([raw])
    display, parsed, kind = _pick_capacity_variant(variants, 0)
    mode_values = _extract_mode_capacity_values(raw)
    mode_values_display = _format_mode_capacity_values(mode_values)
    fallback_mode = _capacity_fallback_mode()

    if kind == "blank":
        return VectorCapacityResolution(
            raw_display=raw_display,
            mode_values_display=mode_values_display,
            selected_mode="",
            selected_display="",
            selected_value=None,
            selected_kind="blank",
            note="",
            reason_code="BLANK",
        )
    if kind == "numeric":
        return VectorCapacityResolution(
            raw_display=raw_display,
            mode_values_display=mode_values_display,
            selected_mode="単一値",
            selected_display=display,
            selected_value=parsed,
            selected_kind=kind,
            note="単一数値を採用",
            reason_code="SINGLE_NUMERIC",
        )
    if kind == "multi":
        return VectorCapacityResolution(
            raw_display=raw_display,
            mode_values_display=mode_values_display,
            selected_mode="未確定",
            selected_display=display,
            selected_value=parsed,
            selected_kind=kind,
            note="カンマ区切りの複数候補",
            reason_code="MULTI_CANDIDATE_COMMA",
        )
    if not mode_values:
        return VectorCapacityResolution(
            raw_display=raw_display,
            mode_values_display=mode_values_display,
            selected_mode="未確定",
            selected_display=display,
            selected_value=parsed,
            selected_kind=kind,
            note="数値化できない表記",
            reason_code="NON_NUMERIC_TEXT",
        )
    if len(mode_values) == 1:
        mode = next(iter(mode_values.keys()))
        value = mode_values[mode]
        return VectorCapacityResolution(
            raw_display=raw_display,
            mode_values_display=mode_values_display,
            selected_mode=mode,
            selected_display=_format_number(value),
            selected_value=value,
            selected_kind="numeric",
            note=f"モード容量1候補のため({mode})を採用",
            reason_code="MODE_SINGLE_CANDIDATE",
        )

    hinted_mode, hinted_keyword, hint_ambiguous = _infer_capacity_mode_from_name(vector_name)
    if hinted_mode:
        hinted_value = mode_values.get(hinted_mode)
        if hinted_value is not None:
            return VectorCapacityResolution(
                raw_display=raw_display,
                mode_values_display=mode_values_display,
                selected_mode=hinted_mode,
                selected_display=_format_number(hinted_value),
                selected_value=hinted_value,
                selected_kind="numeric",
                note=f"機器名称ヒント({hinted_keyword})で({hinted_mode})を採用",
                reason_code="MODE_BY_NAME_HINT",
            )

    if not hint_ambiguous and fallback_mode == CAPACITY_FALLBACK_MAX:
        max_mode, max_value, max_modes = _pick_unique_max_mode(mode_values)
        if max_mode is not None and max_value is not None:
            return VectorCapacityResolution(
                raw_display=raw_display,
                mode_values_display=mode_values_display,
                selected_mode=f"最大値({max_mode})",
                selected_display=_format_number(max_value),
                selected_value=max_value,
                selected_kind="numeric",
                note="機器名称からモード特定不可のため最大値を採用",
                reason_code="MODE_BY_MAX_FALLBACK",
            )
        if max_modes:
            joined_modes = ",".join(mode for mode in MODE_ORDER if mode in max_modes)
            return VectorCapacityResolution(
                raw_display=raw_display,
                mode_values_display=mode_values_display,
                selected_mode="未確定",
                selected_display=display,
                selected_value=parsed,
                selected_kind=kind,
                note=f"機器名称からモード特定不可かつ最大値が複数({joined_modes})",
                reason_code="MODE_MAX_TIE_UNRESOLVED",
            )

    if hint_ambiguous:
        unresolved_note = f"機器名称ヒントが複数({hinted_keyword})でモード未確定"
        unresolved_code = "MODE_HINT_AMBIGUOUS"
    elif fallback_mode == CAPACITY_FALLBACK_STRICT:
        unresolved_note = "機器名称からモード特定不可(strict設定)"
        unresolved_code = "MODE_UNKNOWN_STRICT"
    else:
        unresolved_note = "機器名称からモード特定不可"
        unresolved_code = "MODE_UNKNOWN"

    return VectorCapacityResolution(
        raw_display=raw_display,
        mode_values_display=mode_values_display,
        selected_mode="未確定",
        selected_display=display,
        selected_value=parsed,
        selected_kind=kind,
        note=unresolved_note,
        reason_code=unresolved_code,
    )


def _normalize_trace_value(value: str) -> str:
    text = _normalize_text(value)
    return text if text else "?"


def _format_trace_rows(rows: Iterable[Tuple[str, str, str, str]]) -> str:
    ordered_keys: List[Tuple[str, str, str]] = []
    counts: Dict[Tuple[str, str, str], int] = {}
    for drawing, name, capacity, _voltage in rows:
        key = (
            _normalize_trace_value(drawing),
            _normalize_trace_value(name),
            _normalize_trace_value(capacity),
        )
        if key not in counts:
            ordered_keys.append(key)
            counts[key] = 0
        counts[key] += 1

    if len(ordered_keys) <= 1:
        return ""

    parts: List[str] = []
    for drawing, name, capacity in ordered_keys:
        count = counts[(drawing, name, capacity)]
        label = f"図面:{drawing} 名称:{name} 容量:{capacity}"
        if count > 1:
            label += f" x{count}"
        parts.append(label)
    return " || ".join(parts)


def _normalize_name_for_compare(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.lower()


def _normalize_name_for_output(text: str) -> str:
    normalized = _normalize_text(text)
    return normalized.replace(" ", "").replace("　", "")


def _collect_unique_non_blank(values: Iterable[str]) -> List[str]:
    unique_values: List[str] = []
    seen = set()
    for value in values:
        text = _normalize_text(value)
        if not text:
            continue
        normalized = _normalize_name_for_compare(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_values.append(text)
    return unique_values


def _pick_first_non_blank(values: Iterable[str]) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _normalize_text_for_group_key(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = normalized.replace(" ", "").replace("　", "")
    return normalized.lower()


def _aggregate_judgments(codes: Iterable[JudgmentCode]) -> JudgmentCode:
    code_set = set(codes)
    if "review" in code_set:
        return "review"
    if "mismatch" in code_set:
        return "mismatch"
    return "match"


def _pick_reason(
    overall_code: JudgmentCode,
    legacy_reason: str,
    fallback_reasons: Iterable[str],
) -> str:
    if overall_code == "match":
        return ""
    if legacy_reason:
        return legacy_reason
    for reason in fallback_reasons:
        text = _normalize_text(reason)
        if text:
            return text
    return "判定要確認" if overall_code == "review" else "不一致"


def _evaluate_name(
    *,
    vector_name: str,
    raster_name_candidates: List[str],
    exists_code: JudgmentCode,
) -> Tuple[JudgmentCode, str]:
    if exists_code == "mismatch":
        return "mismatch", "盤表に記載なし"

    if not vector_name or not raster_name_candidates:
        return "review", "名称が不明"

    if len(raster_name_candidates) >= 2:
        return "mismatch", "名称不一致"

    vector_norm = _normalize_name_for_compare(vector_name)
    raster_norm = _normalize_name_for_compare(raster_name_candidates[0])
    if vector_norm == raster_norm:
        return "match", ""
    return "mismatch", "名称不一致"


def _evaluate_quantity(
    *,
    vector_count: Optional[float],
    raster_match_count: int,
    exists_code: JudgmentCode,
) -> Tuple[JudgmentCode, Optional[float], str]:
    count_diff: Optional[float] = None
    if vector_count is not None:
        count_diff = float(raster_match_count) - vector_count

    if exists_code == "mismatch":
        return "mismatch", count_diff, "盤表に記載なし"

    if vector_count is None:
        return "review", count_diff, "台数が不明"

    if count_diff == 0.0:
        return "match", count_diff, ""

    return "mismatch", count_diff, f"台数差分={_format_number(count_diff)}"


def _evaluate_capacity(
    *,
    vector_variant: CapacityVariant,
    raster_variants: List[CapacityVariant],
    exists_code: JudgmentCode,
) -> Tuple[JudgmentCode, Optional[float], str]:
    vector_display, vector_value, vector_kind = vector_variant
    _ = vector_display

    if exists_code == "mismatch":
        return "mismatch", None, "盤表に記載なし"

    if vector_kind == "blank" or not raster_variants:
        return "review", None, "容量欠損"

    if vector_kind == "multi":
        return "review", None, "容量が複数候補"

    if vector_kind == "non_numeric":
        return "review", None, "容量が数値でない"

    if any(kind == "multi" for _, _, kind in raster_variants):
        return "review", None, "容量が複数候補"

    if len(raster_variants) > 1:
        return "review", None, "容量が複数候補"

    raster_display, raster_value, raster_kind = raster_variants[0]
    _ = raster_display
    if raster_kind == "non_numeric":
        return "review", None, "容量が数値でない"

    if raster_kind != "numeric" or vector_value is None or raster_value is None:
        return "review", None, "容量欠損"

    capacity_diff = raster_value - vector_value
    if abs(capacity_diff) <= EPS_KW:
        return "match", capacity_diff, ""

    return "mismatch", capacity_diff, f"容量差分={_format_number(capacity_diff)}"


def _build_legacy_reason(
    *,
    overall_code: JudgmentCode,
    exists_code: JudgmentCode,
    qty_code: JudgmentCode,
    qty_reason: str,
    count_diff: Optional[float],
    capacity_code: JudgmentCode,
    capacity_reason: str,
    name_code: JudgmentCode,
    name_reason: str,
) -> str:
    review_reasons: List[str] = []
    mismatch_reasons: List[str] = []

    if exists_code == "mismatch":
        mismatch_reasons.append("盤表に記載なし")

    if qty_code == "review":
        review_reasons.append("台数差分=欠損")
    elif qty_code == "mismatch":
        mismatch_reasons.append(qty_reason or f"台数差分={_format_number(count_diff)}")

    if capacity_code == "review":
        if capacity_reason in {"容量が複数候補", "容量が数値でない"}:
            review_reasons.append(capacity_reason)
        else:
            review_reasons.append("容量欠損")
    elif capacity_code == "mismatch":
        mismatch_reasons.append(capacity_reason or "容量不一致")

    if name_code == "review":
        review_reasons.append(name_reason or "名称が不明")
    elif name_code == "mismatch":
        mismatch_reasons.append(name_reason or "名称不一致")

    if overall_code == "review":
        if review_reasons:
            return review_reasons[0]
        if mismatch_reasons:
            return mismatch_reasons[0]
        return ""

    if mismatch_reasons:
        return mismatch_reasons[0]
    if review_reasons:
        return review_reasons[0]
    return ""


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
    vector_drawing_number_header = _resolve_header(vector_headers, "vector_drawing_number")
    if not vector_id_header or not vector_power_header or not vector_count_header:
        raise ValueError("Vector CSV required headers are missing.")

    vector_drawing_agg: Dict[str, List[str]] = {}
    if vector_drawing_number_header:
        for row in vector_rows:
            key = _normalize_key(row.get(vector_id_header, ""))
            if not key:
                continue
            vector_drawing_agg.setdefault(key, []).append(row.get(vector_drawing_number_header, ""))

    raster_id_header = _resolve_header(raster_headers, "equipment_id")
    raster_name_header = _resolve_header(raster_headers, "raster_name")
    raster_voltage_header = _resolve_header(raster_headers, "raster_voltage")
    raster_capacity_header = _resolve_header(raster_headers, "raster_capacity_kw")
    raster_drawing_number_header = _resolve_header(raster_headers, "raster_drawing_number")
    if (
        not raster_id_header
        or not raster_name_header
        or not raster_voltage_header
        or not raster_capacity_header
    ):
        raise ValueError("Raster CSV required headers are missing.")

    raster_agg: Dict[str, Dict[str, object]] = {}
    raster_missing_id_agg: Dict[str, Dict[str, object]] = {}
    for row in raster_rows:
        key = _normalize_key(row.get(raster_id_header, ""))
        if not key:
            raster_name_raw = row.get(raster_name_header, "")
            raster_voltage_raw = row.get(raster_voltage_header, "")
            raster_capacity_raw = row.get(raster_capacity_header, "")
            raster_drawing_raw = (
                row.get(raster_drawing_number_header, "") if raster_drawing_number_header else ""
            )
            if not _pick_first_non_blank(
                [raster_name_raw, raster_voltage_raw, raster_capacity_raw, raster_drawing_raw]
            ):
                continue

            raster_capacity_display = _normalize_text(raster_capacity_raw)
            missing_key = "|".join(
                [
                    _normalize_name_for_compare(raster_name_raw),
                    _normalize_text_for_group_key(raster_voltage_raw),
                    _normalize_text_for_group_key(raster_capacity_display),
                    _normalize_text_for_group_key(raster_drawing_raw),
                ]
            )
            missing_agg = raster_missing_id_agg.get(missing_key)
            if missing_agg is None:
                missing_agg = {
                    "name": _pick_first_non_blank([raster_name_raw]),
                    "capacity_display": raster_capacity_display,
                    "drawing_number": _pick_first_non_blank([raster_drawing_raw]),
                    "trace_rows": [],
                    "count": 0,
                }
                raster_missing_id_agg[missing_key] = missing_agg

            missing_agg["count"] = int(missing_agg["count"]) + 1
            missing_agg["trace_rows"].append(  # type: ignore[index]
                (raster_drawing_raw, raster_name_raw, raster_capacity_raw, raster_voltage_raw)
            )
            if not missing_agg["name"]:
                missing_agg["name"] = _pick_first_non_blank([raster_name_raw])
            if not missing_agg["drawing_number"]:
                missing_agg["drawing_number"] = _pick_first_non_blank([raster_drawing_raw])
            continue

        agg = raster_agg.get(key)
        if agg is None:
            agg = {
                "equipment_ids": [],
                "names": [],
                "voltages": [],
                "capacity_values": [],
                "drawing_numbers": [],
                "trace_rows": [],
                "match_count": 0,
            }
            raster_agg[key] = agg

        agg["match_count"] = int(agg["match_count"]) + 1
        raster_drawing_raw = row.get(raster_drawing_number_header, "") if raster_drawing_number_header else ""
        agg["equipment_ids"].append(row.get(raster_id_header, ""))  # type: ignore[index]
        agg["names"].append(row.get(raster_name_header, ""))  # type: ignore[index]
        agg["voltages"].append(row.get(raster_voltage_header, ""))  # type: ignore[index]
        capacity_raw = row.get(raster_capacity_header, "")
        agg["capacity_values"].append(capacity_raw)  # type: ignore[index]
        if raster_drawing_number_header:
            agg["drawing_numbers"].append(raster_drawing_raw)  # type: ignore[index]
        agg["trace_rows"].append(  # type: ignore[index]
            (
                raster_drawing_raw,
                row.get(raster_name_header, ""),
                capacity_raw,
                row.get(raster_voltage_header, ""),
            )
        )

    out_rows: List[Dict[str, str]] = []
    vector_keys: set[str] = set()
    for vector_row in vector_rows:
        vector_equipment_id = vector_row.get(vector_id_header, "")
        key = _normalize_key(vector_equipment_id)
        if key:
            vector_keys.add(key)
        agg = raster_agg.get(key)
        id_match_mark = "◯" if agg is not None else "✗"

        power_per_unit_raw = vector_row.get(vector_power_header, "")
        vector_count = _parse_number(vector_row.get(vector_count_header, ""))
        vector_name_raw = vector_row.get(vector_name_header, "") if vector_name_header else ""
        vector_name = _normalize_name_for_output(vector_name_raw)
        vector_capacity = _resolve_vector_capacity(
            power_per_unit_raw,
            vector_name_raw,
        )

        exists_code: JudgmentCode = "match" if agg else "mismatch"
        raster_match_count = int(agg["match_count"]) if agg else 0

        raster_capacity_variants: List[CapacityVariant] = []
        raster_name_candidates: List[str] = []
        raster_name_candidates_display = ""
        drawing_number = ""
        raster_trace = ""
        if agg:
            raster_capacity_variants = _collect_capacity_variants(agg["capacity_values"])  # type: ignore[arg-type]
            raster_name_candidates = _collect_unique_non_blank(agg["names"])  # type: ignore[arg-type]
            raster_name_candidates_display = ",".join(raster_name_candidates)
            drawing_numbers = _collect_unique_non_blank(agg["drawing_numbers"])  # type: ignore[arg-type]
            drawing_number = ",".join(drawing_numbers)
            raster_trace = _format_trace_rows(agg["trace_rows"])  # type: ignore[arg-type]

        vector_drawing_number = ""
        if key in vector_drawing_agg:
            vector_drawing_numbers = _collect_unique_non_blank(vector_drawing_agg[key])
            vector_drawing_number = ",".join(vector_drawing_numbers)

        qty_code, count_diff, qty_reason = _evaluate_quantity(
            vector_count=vector_count,
            raster_match_count=raster_match_count,
            exists_code=exists_code,
        )
        capacity_code, capacity_diff, capacity_reason = _evaluate_capacity(
            vector_variant=(
                vector_capacity.selected_display,
                vector_capacity.selected_value,
                vector_capacity.selected_kind,
            ),
            raster_variants=raster_capacity_variants,
            exists_code=exists_code,
        )
        name_code, name_reason = _evaluate_name(
            vector_name=vector_name,
            raster_name_candidates=raster_name_candidates,
            exists_code=exists_code,
        )

        overall_code = _aggregate_judgments([exists_code, qty_code, capacity_code, name_code])
        legacy_reason = _build_legacy_reason(
            overall_code=overall_code,
            exists_code=exists_code,
            qty_code=qty_code,
            qty_reason=qty_reason,
            count_diff=count_diff,
            capacity_code=capacity_code,
            capacity_reason=capacity_reason,
            name_code=name_code,
            name_reason=name_reason,
        )
        judgment_reason = _pick_reason(
            overall_code,
            legacy_reason,
            [qty_reason, capacity_reason, name_reason],
        )

        out_rows.append(
            {
                "総合判定": to_mark(overall_code),
                "台数判定": to_mark(qty_code),
                "容量判定": to_mark(capacity_code),
                "名称判定": to_mark(name_code),
                "機器ID照合": id_match_mark,
                "判定理由": judgment_reason,
                "機器ID": vector_equipment_id,
                "機器表 記載名": vector_name,
                "盤表 記載名": raster_name_candidates_display,
                "機器表 台数": _format_number(vector_count),
                "盤表 台数": str(raster_match_count),
                "台数差": _format_number(count_diff),
                "機器表 消費電力(kW)": vector_capacity.raw_display,
                "機器表 モード容量(kW)": vector_capacity.mode_values_display,
                "機器表 判定モード": vector_capacity.selected_mode,
                "機器表 判定採用容量(kW)": _format_number(vector_capacity.selected_value)
                if vector_capacity.selected_kind == "numeric"
                else "",
                "容量判定補足": vector_capacity.note,
                "盤表 容量(kW)": _join_capacity_variants(raster_capacity_variants),
                "容量差(kW)": _format_number(capacity_diff),
                "機器表 図面番号": vector_drawing_number,
                "盤表 図面番号": drawing_number,
                "盤表 記載トレース": raster_trace,
            }
        )

    for key, agg in raster_agg.items():
        if key in vector_keys:
            continue

        equipment_id = _pick_first_non_blank(agg["equipment_ids"]) or key  # type: ignore[arg-type]
        raster_match_count = int(agg["match_count"])
        raster_capacity_variants = _collect_capacity_variants(agg["capacity_values"])  # type: ignore[arg-type]
        raster_name_candidates = _collect_unique_non_blank(agg["names"])  # type: ignore[arg-type]
        raster_name_candidates_display = ",".join(raster_name_candidates)
        drawing_numbers = _collect_unique_non_blank(agg["drawing_numbers"])  # type: ignore[arg-type]
        drawing_number = ",".join(drawing_numbers)
        raster_trace = _format_trace_rows(agg["trace_rows"])  # type: ignore[arg-type]

        out_rows.append(
            {
                "総合判定": to_mark("mismatch"),
                "台数判定": to_mark("mismatch"),
                "容量判定": to_mark("mismatch"),
                "名称判定": to_mark("mismatch"),
                "機器ID照合": "✗",
                "判定理由": "機器表に記載なし",
                "機器ID": equipment_id,
                "機器表 記載名": "",
                "盤表 記載名": raster_name_candidates_display,
                "機器表 台数": "",
                "盤表 台数": str(raster_match_count),
                "台数差": "",
                "機器表 消費電力(kW)": "",
                "機器表 モード容量(kW)": "",
                "機器表 判定モード": "",
                "機器表 判定採用容量(kW)": "",
                "容量判定補足": "",
                "盤表 容量(kW)": _join_capacity_variants(raster_capacity_variants),
                "容量差(kW)": "",
                "機器表 図面番号": "",
                "盤表 図面番号": drawing_number,
                "盤表 記載トレース": raster_trace,
            }
        )

    for agg in raster_missing_id_agg.values():
        raster_trace = _format_trace_rows(agg["trace_rows"])  # type: ignore[arg-type]
        out_rows.append(
            {
                "総合判定": to_mark("review"),
                "台数判定": to_mark("review"),
                "容量判定": to_mark("review"),
                "名称判定": to_mark("review"),
                "機器ID照合": "✗",
                "判定理由": "盤表ID未記載",
                "機器ID": "",
                "機器表 記載名": "",
                "盤表 記載名": str(agg["name"]),
                "機器表 台数": "",
                "盤表 台数": str(int(agg["count"])),
                "台数差": "",
                "機器表 消費電力(kW)": "",
                "機器表 モード容量(kW)": "",
                "機器表 判定モード": "",
                "機器表 判定採用容量(kW)": "",
                "容量判定補足": "",
                "盤表 容量(kW)": str(agg["capacity_display"]),
                "容量差(kW)": "",
                "機器表 図面番号": "",
                "盤表 図面番号": str(agg["drawing_number"]),
                "盤表 記載トレース": raster_trace,
            }
        )

    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    out_columns = OUTPUT_COLUMNS
    with out_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
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
