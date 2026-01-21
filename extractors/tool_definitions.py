from __future__ import annotations

from typing import Any, Dict, Callable

from google.genai import types

from extractors import skills


calculate_area_func = types.FunctionDeclaration(
    name="calculate_area",
    description="幅と高さから面積(m2)を計算する。寸法線の検算に使用。",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "width": types.Schema(type=types.Type.NUMBER),
            "height": types.Schema(type=types.Type.NUMBER),
        },
        required=["width", "height"],
    ),
)

convert_tsubo_to_m2_func = types.FunctionDeclaration(
    name="convert_tsubo_to_m2",
    description="坪数をm2に換算する。",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "tsubo": types.Schema(type=types.Type.NUMBER),
        },
        required=["tsubo"],
    ),
)

calculate_tatami_area_m2_func = types.FunctionDeclaration(
    name="calculate_tatami_area_m2",
    description="帖数から面積(m2)を簡易換算する。",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "tatami": types.Schema(type=types.Type.NUMBER),
        },
        required=["tatami"],
    ),
)

validate_area_sum_func = types.FunctionDeclaration(
    name="validate_area_sum",
    description="複数の部屋面積の合計と期待値を比較し差異を返す。",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "room_areas": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.NUMBER),
            ),
            "expected_total": types.Schema(type=types.Type.NUMBER),
        },
        required=["room_areas", "expected_total"],
    ),
)

TOOLS = [
    types.Tool(
        function_declarations=[
            calculate_area_func,
            convert_tsubo_to_m2_func,
            calculate_tatami_area_m2_func,
            validate_area_sum_func,
        ]
    )
]

SKILL_REGISTRY: Dict[str, Callable[..., Any]] = {
    "calculate_area": skills.calculate_area,
    "convert_tsubo_to_m2": skills.convert_tsubo_to_m2,
    "calculate_tatami_area_m2": skills.calculate_tatami_area_m2,
    "validate_area_sum": skills.validate_area_sum,
}
