from __future__ import annotations

from typing import Dict, List, Iterable


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


def calculate_room_area_from_dimensions(
    room_name: str,
    width_mm: float,
    depth_mm: float,
    calculation_note: str = "",
) -> Dict[str, float | str]:
    width_m = round(width_mm / 1000, 3)
    depth_m = round(depth_mm / 1000, 3)
    area_m2 = round(width_m * depth_m, 2)
    tatami = round(area_m2 / 1.62, 1)
    return {
        "room_name": room_name,
        "width_m": width_m,
        "depth_m": depth_m,
        "area_m2": area_m2,
        "tatami": tatami,
        "calculation": f"{width_m}m × {depth_m}m = {area_m2}㎡",
        "note": calculation_note,
    }


def calculate_composite_area(
    room_name: str,
    areas: Iterable[Dict[str, float | str]],
) -> Dict[str, float | str | List[Dict[str, float | str]]]:
    total_area = 0.0
    steps: List[Dict[str, float | str]] = []
    for area in areas:
        width_mm = float(area.get("width_mm", 0) or 0)
        depth_mm = float(area.get("depth_mm", 0) or 0)
        operation = str(area.get("operation", "add") or "add").lower()
        width_m = round(width_mm / 1000, 3)
        depth_m = round(depth_mm / 1000, 3)
        piece_area = round(width_m * depth_m, 2)
        if operation == "subtract":
            total_area -= piece_area
        else:
            operation = "add"
            total_area += piece_area
        steps.append({
            "operation": operation,
            "width_m": width_m,
            "depth_m": depth_m,
            "area_m2": piece_area,
            "calculation": f"{width_m}m × {depth_m}m = {piece_area}㎡",
        })

    total_area = round(total_area, 2)
    tatami = round(total_area / 1.62, 1)
    return {
        "room_name": room_name,
        "area_m2": total_area,
        "tatami": tatami,
        "steps": steps,
    }
