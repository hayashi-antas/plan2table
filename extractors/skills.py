from __future__ import annotations

from typing import Dict, List


def calculate_area(width: float, height: float) -> float:
    return round(width * height, 2)


def convert_tsubo_to_m2(tsubo: float) -> float:
    return round(tsubo * 3.30579, 2)


def calculate_tatami_area_m2(tatami: float) -> float:
    # 1帖あたり約1.62㎡の簡易換算
    return round(tatami * 1.62, 2)


def validate_area_sum(room_areas: List[float], expected_total: float) -> Dict[str, float | bool]:
    actual = round(sum(room_areas), 2)
    diff = round(abs(actual - expected_total), 2)
    return {
        "actual_sum": actual,
        "expected_total": round(expected_total, 2),
        "difference": diff,
        "is_valid": diff < 0.5,
    }
