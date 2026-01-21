from __future__ import annotations

import re
from typing import Dict, Iterable, Optional, Tuple


_FULLWIDTH_DIGITS = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "．": ".",
        "，": ",",
    }
)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.translate(_FULLWIDTH_DIGITS)
    normalized = normalized.replace("m²", "m2").replace("㎡", "m2")
    normalized = normalized.replace("平米", "m2")
    normalized = normalized.replace("Ｍ２", "m2").replace("ｍ２", "m2")
    return normalized


def _format_number(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".")


def _parse_area(value: str, unit: str) -> Optional[str]:
    try:
        numeric = float(value)
    except ValueError:
        return None
    if unit == "坪":
        numeric *= 3.31
    return _format_number(numeric)


def _scan_patterns(text: str, patterns: Iterable[Tuple[str, re.Pattern]]) -> Dict[str, str]:
    results: Dict[str, str] = {}
    for key, pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = match.group("value")
        unit = match.group("unit")
        parsed = _parse_area(value, unit)
        if parsed is not None:
            results[key] = parsed
    return results


def extract_summary_areas(text: str) -> Dict[str, str]:
    normalized = normalize_text(text)
    patterns = [
        (
            "exclusive_area_m2",
            re.compile(
                r"(専有面積|専用面積|住戸専用面積|住戸面積)\s*[:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|坪)"
            ),
        ),
        (
            "balcony_area_m2",
            re.compile(
                r"(バルコニー面積|バルコニー|ベランダ|テラス)\s*[:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|坪)"
            ),
        ),
        (
            "total_area_m2",
            re.compile(
                r"(延床面積|床面積合計|合計面積)\s*[:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m2|坪)"
            ),
        ),
    ]
    return _scan_patterns(normalized, patterns)
