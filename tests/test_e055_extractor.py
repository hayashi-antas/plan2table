from extractors.e055_extractor import (
    RowCluster,
    WordBox,
    _cleanup_model_text,
    _cluster_x_positions,
    _extract_candidates_from_cluster,
    _propagate_equipment_in_section,
    build_output_rows,
    split_equivalent_model,
    strip_times_marker_from_model,
)


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


def test_strip_times_marker_removes_only_multiplier_markers():
    assert strip_times_marker_from_model("NNN111 ×2") == "NNN111"
    assert strip_times_marker_from_model("NNN111 x3") == "NNN111"
    assert strip_times_marker_from_model("NNN111 / OD222 ×4") == "NNN111 / OD222"


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
    assert rows[0]["相当型番"] == "TAD-ELT7W1-122J27-24A"
    assert rows[1]["機器器具"] == ""
    assert rows[1]["相当型番"] == "TAD-ELT7W1-026J27-24A"


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
