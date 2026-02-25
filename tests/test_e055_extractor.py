# ruff: noqa: RUF001

from extractors.e055_extractor import (
    LineAssistConfig,
    RowCluster,
    WordBox,
    _apply_line_assist_if_confident,
    _char_pos_to_token_index,
    _cleanup_model_text,
    _cluster_x_positions,
    _extract_candidates_from_cluster,
    _propagate_equipment_in_section,
    _should_run_line_assist,
    build_output_rows,
    split_equivalent_model,
    strip_times_marker_from_model,
)
from PIL import Image
from pathlib import Path


def test_split_equivalent_model_ascii_colon():
    maker, model = split_equivalent_model("Panasonic:NNN111")
    assert maker == "Panasonic"
    assert model == "NNN111"


def test_split_equivalent_model_fullwidth_colon():
    maker, model = split_equivalent_model("ODELIC：OD222")
    assert maker == "ODELIC"
    assert model == "OD222"


def test_split_equivalent_model_without_colon_fallback():
    maker, model = split_equivalent_model("NNN111")
    assert maker == ""
    assert model == "NNN111"


def test_char_pos_to_token_index_falls_back_to_last_token():
    assert _char_pos_to_token_index(["DAIKO", ":", "LZA-93039"], 999) == 2
    assert _char_pos_to_token_index([], 999) == 0


def test_strip_times_marker_keeps_multiplier_markers():
    assert strip_times_marker_from_model("NNN111 ×2") == "NNN111 ×2"
    assert strip_times_marker_from_model("NNN111 x3") == "NNN111 x3"
    assert strip_times_marker_from_model("NNN111 / OD222 ×4") == "NNN111 / OD222 ×4"


def test_split_equivalent_model_keeps_multiplier_variants():
    maker, model = split_equivalent_model("Panasonic:NNN111×2")
    assert maker == "Panasonic"
    assert model == "NNN111×2"

    maker, model = split_equivalent_model("Panasonic:NNN111 x3")
    assert maker == "Panasonic"
    assert model == "NNN111 x3"

    maker, model = split_equivalent_model("Panasonic:NNN111 ✕4")
    assert maker == "Panasonic"
    assert model == "NNN111 ✕4"

    maker, model = split_equivalent_model("Panasonic:NNN111 (x2)")
    assert maker == "Panasonic"
    assert model == "NNN111 (x2)"


def test_split_equivalent_model_keeps_multi_pair_in_single_cell():
    maker, model = split_equivalent_model("Panasonic:NNN111 / ODELIC:OD222")
    assert maker == "Panasonic"
    assert model == "NNN111 / ODELIC:OD222"


def _word(text: str, cx: float, cy: float = 100.0) -> WordBox:
    return WordBox(text=text, cx=cx, cy=cy, bbox=(cx - 5.0, cy - 5.0, cx + 5.0, cy + 5.0))


def test_build_output_rows_keeps_duplicates_and_page_order():
    input_rows = [
        {"page": 2, "section_index": 0, "block_index": 0, "row_y": 40.0, "row_x": 10.0, "機器器具": "器具B", "相当型番": "B:BBB"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 30.0, "row_x": 10.0, "機器器具": "器具A", "相当型番": "A:AAA"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 30.0, "row_x": 10.0, "機器器具": "器具A", "相当型番": "A:AAA"},
    ]
    rows = build_output_rows(input_rows)
    assert rows == [
        {"機器器具": "器具A", "メーカー": "A", "型番": "AAA"},
        {"機器器具": "器具A", "メーカー": "A", "型番": "AAA"},
        {"機器器具": "器具B", "メーカー": "B", "型番": "BBB"},
    ]


def test_build_output_rows_prioritizes_block_then_row_for_human_reading_order():
    input_rows = [
        {"page": 1, "section_index": 0, "block_index": 1, "row_y": 100.0, "row_x": 900.0, "機器器具": "UK1", "相当型番": "Panasonic:XLX420PENT-LE9"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 120.0, "row_x": 120.0, "機器器具": "CT2", "相当型番": "Panasonic:XLX420NENP-LE9"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 80.0, "row_x": 110.0, "機器器具": "CT1", "相当型番": "Panasonic:XLX210NENC-LE9"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 100.0, "row_x": 115.0, "機器器具": "CT1g", "相当型番": "同上ガード付"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 140.0, "row_x": 125.0, "機器器具": "CT2g", "相当型番": "同上ガード付"},
    ]
    rows = build_output_rows(input_rows)
    assert [row["機器器具"] for row in rows] == ["CT1", "CT1g", "CT2", "CT2g", "UK1"]


def test_build_output_rows_skips_emergency_certification_rows():
    input_rows = [
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 80.0, "row_x": 100.0, "機器器具": "EDL", "相当型番": "LALE-004"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 90.0, "row_x": 110.0, "機器器具": "ES1", "相当型番": "LALE-015"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 100.0, "row_x": 120.0, "機器器具": "UK1", "相当型番": "Panasonic:XLX420PENT-LE9"},
    ]
    rows = build_output_rows(input_rows)
    assert rows == [
        {"機器器具": "UK1", "メーカー": "Panasonic", "型番": "XLX420PENT-LE9"},
    ]


def test_build_output_rows_skips_lale_model_even_for_non_excluded_equipment():
    input_rows = [
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 80.0, "row_x": 100.0, "機器器具": "TP1", "相当型番": "LALE-004"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 100.0, "row_x": 120.0, "機器器具": "UK1", "相当型番": "Panasonic:XLX420PENT-LE9"},
    ]
    rows = build_output_rows(input_rows)
    assert rows == [
        {"機器器具": "UK1", "メーカー": "Panasonic", "型番": "XLX420PENT-LE9"},
    ]


def test_build_output_rows_skips_empty_model_rows():
    input_rows = [
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 80.0, "row_x": 100.0, "機器器具": "TP1", "相当型番": ""},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 90.0, "row_x": 110.0, "機器器具": "TP2", "相当型番": "Panasonic:"},
        {"page": 1, "section_index": 0, "block_index": 0, "row_y": 100.0, "row_x": 120.0, "機器器具": "UK1", "相当型番": "Panasonic:XLX420PENT-LE9"},
    ]
    rows = build_output_rows(input_rows)
    assert rows == [
        {"機器器具": "UK1", "メーカー": "Panasonic", "型番": "XLX420PENT-LE9"},
    ]


def test_cluster_x_positions_groups_nearby_values():
    centers = _cluster_x_positions([100.0, 120.0, 620.0, 650.0, 1400.0], tolerance=80.0)
    assert len(centers) == 3
    assert centers[0] == 110.0
    assert centers[1] == 635.0
    assert centers[2] == 1400.0


def test_extract_candidates_from_cluster_includes_doujou_guard_variant():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("CT1g", 100.0),
            _word("Hf16×1形", 180.0),
            _word("LED", 260.0),
            _word("12.1W", 330.0),
            _word("同上", 410.0),
            _word("犬-F", 480.0),
            _word("付", 530.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "CT1g"
    assert rows[0]["相当型番"] == "同上ガード付"


def test_extract_candidates_from_cluster_includes_plain_doujou():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("CT2g", 100.0),
            _word("同上", 180.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "CT2g"
    assert rows[0]["相当型番"] == "同上"


def test_extract_candidates_from_cluster_normalizes_ocr_guard_variant():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("CT2g", 100.0),
            _word("同上", 180.0),
            _word("廿一卡付", 260.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "CT2g"
    assert rows[0]["相当型番"] == "同上ガード付"


def test_extract_candidates_from_cluster_normalizes_ocr_katakana_confusion_guard_variant():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("CT2g", 100.0),
            _word("同上", 180.0),
            _word("力", 260.0),
            _word("一", 290.0),
            _word("ľ", 320.0),
            _word("付", 350.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "CT2g"
    assert rows[0]["相当型番"] == "同上ガード付"


def test_cleanup_model_text_keeps_decimal_power_text():
    text = _cleanup_model_text("11.6W×6 TAD - ELT7W1-146J27-24A ×6")
    assert text == "11.6W×6 TAD-ELT7W1-146J27-24A ×6"


def test_extract_candidates_from_cluster_handles_model_only_continuation_row():
    cluster = RowCluster(
        row_y=200.0,
        words=[
            _word("9.6W×1", 100.0, cy=200.0),
            _word("TAD", 180.0, cy=200.0),
            _word("-", 220.0, cy=200.0),
            _word("ELT7W1-122J27-24A", 320.0, cy=200.0),
            _word("×", 430.0, cy=200.0),
            _word("1", 455.0, cy=200.0),
            _word("TAD", 650.0, cy=200.0),
            _word("-", 690.0, cy=200.0),
            _word("ELT7W1-026J27-24A", 800.0, cy=200.0),
            _word("×", 915.0, cy=200.0),
            _word("1", 940.0, cy=200.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 2
    assert rows[0]["機器器具"] == ""
    assert rows[0]["相当型番"] == "TAD-ELT7W1-122J27-24A × 1"
    assert rows[1]["機器器具"] == ""
    assert rows[1]["相当型番"] == "TAD-ELT7W1-026J27-24A × 1"


def test_extract_candidates_from_cluster_handles_dash_variant_in_model_only_continuation_row():
    cluster = RowCluster(
        row_y=200.0,
        words=[
            _word("9.6W×1", 100.0, cy=200.0),
            _word("TAD", 180.0, cy=200.0),
            _word("−", 220.0, cy=200.0),
            _word("ELT7W1-122J27-24A", 320.0, cy=200.0),
            _word("×", 430.0, cy=200.0),
            _word("1", 455.0, cy=200.0),
            _word("TAD", 650.0, cy=200.0),
            _word("-", 690.0, cy=200.0),
            _word("ELT7W1-026J27-24A", 800.0, cy=200.0),
            _word("×", 915.0, cy=200.0),
            _word("1", 940.0, cy=200.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 2
    assert rows[0]["相当型番"] == "TAD-ELT7W1-122J27-24A × 1"
    assert rows[1]["相当型番"] == "TAD-ELT7W1-026J27-24A × 1"


def test_extract_candidates_from_cluster_keeps_multiplier_suffix_without_colon():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("TP1", 100.0),
            _word("TAD", 180.0),
            _word("-", 220.0),
            _word("ELT7W1-146J27-24A", 320.0),
            _word("(x2)", 430.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "TP1"
    assert rows[0]["相当型番"] == "TAD-ELT7W1-146J27-24A (x2)"


def test_extract_candidates_from_cluster_handles_colon_model_only_continuation_row():
    cluster = RowCluster(
        row_y=120.0,
        words=[
            _word("DAIKO", 240.0, cy=120.0),
            _word(":", 300.0, cy=120.0),
            _word("LZA-93039", 380.0, cy=120.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == ""
    assert rows[0]["相当型番"] == "DAIKO:LZA-93039"


def test_extract_candidates_from_cluster_prioritizes_colon_model_with_wattage_row():
    cluster = RowCluster(
        row_y=120.0,
        words=[
            _word("9.8W×3", 120.0, cy=120.0),
            _word("DAIKO", 240.0, cy=120.0),
            _word(":", 300.0, cy=120.0),
            _word("LZD-93548ABB", 380.0, cy=120.0),
            _word("×", 500.0, cy=120.0),
            _word("3", 520.0, cy=120.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == ""
    assert rows[0]["相当型番"] == "DAIKO:LZD-93548ABB × 3"


def test_extract_candidates_from_cluster_sets_model_x_to_model_column_for_colon_row():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("DL9", 100.0),
            _word("DAIKO", 240.0),
            _word(":", 300.0),
            _word("LZD-93548ABB", 380.0),
            _word("×", 500.0),
            _word("3", 520.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "DL9"
    assert rows[0]["相当型番"] == "DAIKO:LZD-93548ABB × 3"
    assert rows[0]["row_x"] == 95.0
    assert rows[0]["model_x"] == 235.0


def test_extract_candidates_from_cluster_sets_model_x_to_model_column_for_non_colon_row():
    cluster = RowCluster(
        row_y=100.0,
        words=[
            _word("TP1", 100.0),
            _word("LED", 200.0),
            _word("11.6W×6", 280.0),
            _word("TAD", 380.0),
            _word("-", 420.0),
            _word("ELT7W1-146J27-24A", 520.0),
            _word("×", 640.0),
            _word("6", 660.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert len(rows) == 1
    assert rows[0]["機器器具"] == "TP1"
    assert rows[0]["相当型番"] == "TAD-ELT7W1-146J27-24A × 6"
    assert rows[0]["row_x"] == 95.0
    assert rows[0]["model_x"] == 375.0


def test_build_output_rows_assigns_dl9_to_colon_model_only_continuation_row():
    section_candidates = [
        {"row_y": 100.0, "row_x": 100.0, "block_index": 0, "機器器具": "DL9", "相当型番": "DAIKO:LZD-93548ABB × 3"},
        {"row_y": 120.0, "row_x": 240.0, "block_index": 0, "機器器具": "", "相当型番": "DAIKO:LZA-93039"},
    ]
    _propagate_equipment_in_section(section_candidates)
    rows = build_output_rows(section_candidates)
    assert rows == [
        {"機器器具": "DL9", "メーカー": "DAIKO", "型番": "LZD-93548ABB × 3"},
        {"機器器具": "DL9", "メーカー": "DAIKO", "型番": "LZA-93039"},
    ]


def test_propagate_equipment_in_section_assigns_previous_equipment_in_same_block():
    section_candidates = [
        {"row_y": 100.0, "row_x": 100.0, "block_index": 0, "機器器具": "TP1", "相当型番": "TAD-ELT7W1-146J27-24A"},
        {"row_y": 120.0, "row_x": 120.0, "block_index": 0, "機器器具": "", "相当型番": "TAD-ELT7W1-122J27-24A"},
        {"row_y": 100.0, "row_x": 600.0, "block_index": 1, "機器器具": "TP2", "相当型番": "TAD-ELT7W1-146J27-24A"},
        {"row_y": 120.0, "row_x": 620.0, "block_index": 1, "機器器具": "", "相当型番": "TAD-ELT7W1-026J27-24A"},
    ]
    _propagate_equipment_in_section(section_candidates)
    assert section_candidates[1]["機器器具"] == "TP1"
    assert section_candidates[3]["機器器具"] == "TP2"


def test_propagate_equipment_in_section_maps_continuation_row_by_left_to_right_order():
    section_candidates = [
        {"row_y": 100.0, "row_x": 260.0, "block_index": 0, "機器器具": "TP1", "相当型番": "TAD-ELT7W1-146J27-24A"},
        {"row_y": 100.0, "row_x": 820.0, "block_index": 1, "機器器具": "TP2", "相当型番": "TAD-ELT7W1-146J27-24A"},
        {"row_y": 120.0, "row_x": 640.0, "block_index": 1, "機器器具": "", "相当型番": "TAD-ELT7W1-122J27-24A"},
        {"row_y": 120.0, "row_x": 1140.0, "block_index": 2, "機器器具": "", "相当型番": "TAD-ELT7W1-026J27-24A"},
    ]
    _propagate_equipment_in_section(section_candidates)
    continuation_rows = sorted(
        [row for row in section_candidates if row["row_y"] == 120.0],
        key=lambda item: item["row_x"],
    )
    assert continuation_rows[0]["機器器具"] == "TP1"
    assert continuation_rows[1]["機器器具"] == "TP2"


def test_propagate_equipment_in_section_maps_single_continuation_row_by_nearest_model_x():
    section_candidates = [
        {"row_y": 100.0, "row_x": 273.0, "model_x": 640.0, "block_index": 0, "機器器具": "DL8", "相当型番": "DAIKO:LZD-93561LWM"},
        {"row_y": 100.0, "row_x": 829.0, "model_x": 1198.0, "block_index": 1, "機器器具": "DL9", "相当型番": "DAIKO:LZD-93548ABB × 3"},
        {"row_y": 100.0, "row_x": 1389.0, "model_x": 1755.0, "block_index": 2, "機器器具": "DL10", "相当型番": "DAIKO:LLD-7141LUM3"},
        {"row_y": 120.0, "row_x": 1176.0, "model_x": 1198.0, "block_index": 2, "機器器具": "", "相当型番": "DAIKO:LZA-93039"},
    ]
    _propagate_equipment_in_section(section_candidates)
    continuation = next(row for row in section_candidates if row["row_y"] == 120.0)
    assert continuation["機器器具"] == "DL9"


def test_propagate_equipment_in_section_avoids_duplicate_source_assignment_when_possible():
    section_candidates = [
        {"row_y": 100.0, "row_x": 100.0, "model_x": 100.0, "block_index": 0, "機器器具": "A1", "相当型番": "A:AA-001"},
        {"row_y": 100.0, "row_x": 300.0, "model_x": 300.0, "block_index": 1, "機器器具": "A2", "相当型番": "A:AA-002"},
        {"row_y": 100.0, "row_x": 500.0, "model_x": 500.0, "block_index": 2, "機器器具": "A3", "相当型番": "A:AA-003"},
        {"row_y": 120.0, "row_x": 320.0, "model_x": 320.0, "block_index": 3, "機器器具": "", "相当型番": "A:AA-101"},
        {"row_y": 120.0, "row_x": 340.0, "model_x": 340.0, "block_index": 4, "機器器具": "", "相当型番": "A:AA-102"},
    ]
    _propagate_equipment_in_section(section_candidates)
    continuation_rows = sorted(
        [row for row in section_candidates if row["row_y"] == 120.0],
        key=lambda item: item["row_x"],
    )
    assert [row["機器器具"] for row in continuation_rows] == ["A2", "A3"]


def test_should_run_line_assist_when_continuation_ratio_is_high():
    section_candidates = [
        {"row_x": 100.0, "model_x": 350.0, "機器器具": "TP1", "相当型番": "TAD-001"},
        {"row_x": 120.0, "model_x": 360.0, "機器器具": "", "相当型番": "TAD-002"},
        {"row_x": 640.0, "model_x": 860.0, "機器器具": "", "相当型番": "DAIKO:LZA-93039"},
    ]
    should_run, reasons = _should_run_line_assist(
        section_candidates,
        x_centers=[110.0, 650.0],
        section_bounds={"x_min": 80.0, "x_max": 1200.0, "y_min": 60.0, "y_max": 240.0},
    )
    assert should_run is True
    assert "high_continuation_ratio" in reasons


def test_should_not_run_line_assist_for_stable_section():
    section_candidates = [
        {"row_x": 100.0, "model_x": 320.0, "機器器具": "DL1", "相当型番": "A:AA-001"},
        {"row_x": 620.0, "model_x": 860.0, "機器器具": "DL2", "相当型番": "A:AA-002"},
        {"row_x": 1140.0, "model_x": 1380.0, "機器器具": "DL3", "相当型番": "A:AA-003"},
    ]
    should_run, reasons = _should_run_line_assist(
        section_candidates,
        x_centers=[110.0, 630.0, 1150.0],
        section_bounds={"x_min": 80.0, "x_max": 1500.0, "y_min": 60.0, "y_max": 240.0},
    )
    assert should_run is False
    assert reasons == []


def test_apply_line_assist_if_confident_adopts_when_quality_improves(monkeypatch):
    section_candidates = [
        {"row_x": 100.0, "model_x": 100.0, "block_index": 0, "機器器具": "TP1", "相当型番": "A:AA-001"},
        {"row_x": 620.0, "model_x": 620.0, "block_index": 0, "機器器具": "TP2", "相当型番": "A:AA-002"},
        {"row_x": 610.0, "model_x": 620.0, "block_index": 0, "機器器具": "", "相当型番": "A:AA-101"},
    ]

    monkeypatch.setattr(
        "extractors.e055_extractor._collect_vector_vertical_lines",
        lambda **kwargs: ([80.0, 400.0, 900.0], {"source": "vector", "raw_lines": 3, "error": ""}),
    )
    monkeypatch.setattr(
        "extractors.e055_extractor._collect_image_vertical_lines",
        lambda **kwargs: ([82.0, 398.0, 902.0], {"source": "image", "raw_lines": 3, "elapsed_ms": 12.0, "timed_out": False, "error": ""}),
    )

    info = _apply_line_assist_if_confident(
        section_candidates=section_candidates,
        section_bounds={"x_min": 60.0, "x_max": 1200.0, "y_min": 40.0, "y_max": 260.0},
        baseline_x_centers=[360.0],
        page_image=Image.new("RGB", (1600, 900), "white"),
        pdf_path=Path("/tmp/non-existent.pdf"),
        page_number=1,
        config=LineAssistConfig(mode="auto", latency_budget_ms=300, min_confidence=0.1, debug_enabled=False),
    )
    assert info["adopted"] is True
    assert int(section_candidates[2]["block_index"]) != 0


def test_apply_line_assist_if_confident_rejects_when_confidence_low(monkeypatch):
    section_candidates = [
        {"row_x": 100.0, "model_x": 100.0, "block_index": 0, "機器器具": "TP1", "相当型番": "A:AA-001"},
        {"row_x": 620.0, "model_x": 620.0, "block_index": 0, "機器器具": "", "相当型番": "A:AA-101"},
    ]

    monkeypatch.setattr(
        "extractors.e055_extractor._collect_vector_vertical_lines",
        lambda **kwargs: ([], {"source": "vector", "raw_lines": 0, "error": ""}),
    )
    monkeypatch.setattr(
        "extractors.e055_extractor._collect_image_vertical_lines",
        lambda **kwargs: ([], {"source": "image", "raw_lines": 0, "elapsed_ms": 20.0, "timed_out": False, "error": ""}),
    )

    info = _apply_line_assist_if_confident(
        section_candidates=section_candidates,
        section_bounds={"x_min": 60.0, "x_max": 1200.0, "y_min": 40.0, "y_max": 260.0},
        baseline_x_centers=[110.0, 620.0],
        page_image=Image.new("RGB", (1600, 900), "white"),
        pdf_path=Path("/tmp/non-existent.pdf"),
        page_number=1,
        config=LineAssistConfig(mode="auto", latency_budget_ms=300, min_confidence=0.9, debug_enabled=False),
    )
    assert info["adopted"] is False
    assert info["rejected_reason"] in {"confidence_below_threshold", "no_line_blocks"}
