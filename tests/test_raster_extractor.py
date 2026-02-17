import csv
from pathlib import Path

from PIL import Image

from extractors.raster_extractor import (
    ColumnBounds,
    WordBox,
    detect_table_candidates_from_page_words,
    extract_raster_pdf,
    extract_drawing_number_from_word_boxes,
    infer_column_bounds,
    infer_dynamic_data_start_y,
    normalize_row_cells,
    rows_from_words,
    resolve_target_pages,
    resolve_drawing_number,
)


def _wb(text: str, cx: float, cy: float, w: float = 16.0, h: float = 8.0) -> WordBox:
    half_w = w / 2.0
    half_h = h / 2.0
    return WordBox(
        text=text,
        cx=cx,
        cy=cy,
        bbox=(cx - half_w, cy - half_h, cx + half_w, cy + half_h),
    )


def test_extract_drawing_number_from_label_and_direct_below():
    words = [
        _wb("図面番号", 540, 120, w=54),
        _wb("E-024", 545, 138, w=36),
        _wb("実施設計図", 540, 170, w=56),
    ]
    drawing_number = extract_drawing_number_from_word_boxes(
        words,
        frame_width=600,
        frame_height=800,
    )
    assert drawing_number == "E-024"


def test_extract_drawing_number_with_split_label_and_split_value():
    words = [
        _wb("図面", 520, 120, w=28),
        _wb("番号", 560, 120, w=28),
        _wb("E", 538, 140, w=12),
        _wb("-", 548, 140, w=8),
        _wb("024", 565, 140, w=20),
    ]
    drawing_number = extract_drawing_number_from_word_boxes(
        words,
        frame_width=600,
        frame_height=800,
    )
    assert drawing_number == "E-024"


def test_extract_drawing_number_fallbacks_to_bottom_right_candidate():
    words = [
        _wb("機器番号", 160, 240, w=40),
        _wb("A-1", 170, 262, w=24),
        _wb("E-024", 560, 740, w=36),
    ]
    drawing_number = extract_drawing_number_from_word_boxes(
        words,
        frame_width=600,
        frame_height=800,
    )
    assert drawing_number == "E-024"


def test_extract_drawing_number_returns_blank_when_not_found():
    words = [
        _wb("図面", 520, 120, w=28),
        _wb("番号", 560, 120, w=28),
        _wb("実施設計図", 540, 170, w=56),
    ]
    drawing_number = extract_drawing_number_from_word_boxes(
        words,
        frame_width=600,
        frame_height=800,
    )
    assert drawing_number == ""


def test_resolve_drawing_number_uses_text_layer_fallback(monkeypatch):
    monkeypatch.setattr(
        "extractors.raster_extractor.extract_drawing_number_from_text_layer",
        lambda pdf_path, page: "M-12A",
    )
    drawing_number, source = resolve_drawing_number(
        pdf_path=Path("/tmp/panel.pdf"),
        page=1,
        right_side_words=[],
        right_side_size=(600, 800),
    )
    assert drawing_number == "M-12A"
    assert source == "text_layer"


def test_resolve_target_pages_handles_all_pages_mode():
    assert resolve_target_pages(total_pages=2, page=0) == [1, 2]
    assert resolve_target_pages(total_pages=2, page=-1) == [1, 2]
    assert resolve_target_pages(total_pages=2, page=1) == [1]


def test_detect_table_candidates_from_multiple_header_clusters():
    words = [
        _wb("機器番号", 80, 80, w=48),
        _wb("名称", 150, 80, w=28),
        _wb("電圧", 210, 80, w=28),
        _wb("容量(kW)", 290, 80, w=52),
        _wb("EF-1", 80, 100, w=34),
        _wb("排風機", 150, 100, w=30),
        _wb("200", 210, 100, w=24),
        _wb("0.75", 290, 100, w=28),
        _wb("機器", 470, 240, w=28),
        _wb("番号", 505, 240, w=28),
        _wb("名", 560, 240, w=16),
        _wb("称", 578, 240, w=16),
        _wb("電", 620, 240, w=16),
        _wb("圧", 638, 240, w=16),
        _wb("容", 670, 240, w=16),
        _wb("量", 688, 240, w=16),
        _wb("PAC-1", 490, 262, w=42),
        _wb("空調室外機", 565, 262, w=52),
        _wb("200", 625, 262, w=24),
        _wb("9.0", 684, 262, w=24),
    ]
    candidates = detect_table_candidates_from_page_words(words, frame_size=(800, 500))
    assert len(candidates) >= 2
    assert candidates[0].header_y < candidates[1].header_y


def test_detect_table_candidates_with_split_header_words():
    words = [
        _wb("機", 80, 80, w=12),
        _wb("器", 94, 80, w=12),
        _wb("番", 108, 80, w=12),
        _wb("号", 122, 80, w=12),
        _wb("名", 170, 80, w=12),
        _wb("称", 184, 80, w=12),
        _wb("電", 228, 80, w=12),
        _wb("圧", 242, 80, w=12),
        _wb("容", 286, 80, w=12),
        _wb("量", 300, 80, w=12),
        _wb("k", 314, 80, w=12),
        _wb("W", 328, 80, w=12),
    ]
    candidates = detect_table_candidates_from_page_words(words, frame_size=(500, 300))
    assert len(candidates) == 1


def test_dynamic_start_extracts_small_single_row_table():
    words = [
        _wb("機器番号", 70, 22, w=48),
        _wb("名称", 160, 22, w=28),
        _wb("電圧", 250, 22, w=28),
        _wb("容量(kW)", 330, 22, w=52),
        _wb("PAC-2", 70, 48, w=42),
        _wb("空調室外機", 160, 48, w=52),
        _wb("200", 250, 48, w=24),
        _wb("6.6", 330, 48, w=24),
    ]
    bounds = infer_column_bounds(words, side_width=420)
    start_y = infer_dynamic_data_start_y(words, bounds.header_y)
    rows = rows_from_words(words, bounds, y_cluster=10.0, start_y=start_y)
    assert len(rows) == 1
    assert rows[0]["機器番号"] == "PAC-2"
    assert rows[0]["容量(kW)"] == "6.6"


def test_rows_from_words_keeps_row_intersecting_start_line():
    words = [
        _wb("機器番号", 70, 22, w=48),
        _wb("名称", 160, 22, w=28),
        _wb("電圧", 250, 22, w=28),
        _wb("容量(kW)", 330, 22, w=52),
        _wb("PAC-2", 70, 70, w=42, h=40),
        _wb("空調室外機", 160, 70, w=52, h=40),
        _wb("200", 250, 70, w=24, h=40),
        _wb("6.6", 330, 70, w=24, h=40),
    ]
    bounds = infer_column_bounds(words, side_width=420)
    rows = rows_from_words(words, bounds, y_cluster=10.0, start_y=81.0)
    assert len(rows) == 1
    assert rows[0]["機器番号"] == "PAC-2"
    assert rows[0]["容量(kW)"] == "6.6"


def test_rows_from_words_ignores_tall_digit_noise_in_capacity():
    words = [
        _wb("機器番号", 70, 22, w=48),
        _wb("名称", 160, 22, w=28),
        _wb("電圧", 250, 22, w=28),
        _wb("容量(kW)", 330, 22, w=52),
        _wb("F-B1-1", 70, 70, w=42),
        _wb("排風機", 160, 70, w=32),
        _wb("3$200V", 250, 70, w=44),
        _wb("222", 322, 70, w=20, h=90),
        _wb("2.2", 334, 70, w=24),
        _wb("CV", 355, 70, w=20),
    ]
    bounds = infer_column_bounds(words, side_width=420)
    rows = rows_from_words(words, bounds, y_cluster=10.0, start_y=40.0)
    assert len(rows) == 1
    assert rows[0]["機器番号"] == "F-B1-1"
    assert rows[0]["容量(kW)"] == "2.2"


def test_extract_raster_pdf_page_zero_merges_all_pages(tmp_path, monkeypatch):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    out_csv = tmp_path / "raster.csv"
    debug_dir = tmp_path / "debug"
    png_path = tmp_path / "dummy.png"
    Image.new("RGB", (20, 20), color=(255, 255, 255)).save(png_path)

    monkeypatch.setattr("extractors.raster_extractor.build_vision_client", lambda _: object())
    monkeypatch.setattr("extractors.raster_extractor.count_pdf_pages", lambda _: 2)
    monkeypatch.setattr("extractors.raster_extractor.run_pdftoppm", lambda *args, **kwargs: png_path)
    monkeypatch.setattr("extractors.raster_extractor.save_debug_image", lambda *args, **kwargs: None)

    def fake_extract_words(client, side_image):
        return [_wb("dummy", 10, 10, w=8, h=8)]

    monkeypatch.setattr("extractors.raster_extractor.extract_words", fake_extract_words)
    monkeypatch.setattr(
        "extractors.raster_extractor.infer_column_bounds",
        lambda words, side_width: ColumnBounds(
            x_min=0.0, b12=5.0, b23=10.0, b34=15.0, x_max=20.0, header_y=0.0
        ),
    )

    call_index = {"n": 0}

    def fake_rows_from_words(words, bounds, y_cluster):
        call_index["n"] += 1
        idx = call_index["n"]
        return [
            {
                "row_index": 1,
                "row_y": 100.0,
                "機器番号": f"A-{idx}",
                "機器名称": "送風機",
                "電圧(V)": "200",
                "容量(kW)": "1.5",
            }
        ]

    monkeypatch.setattr("extractors.raster_extractor.rows_from_words", fake_rows_from_words)

    def fake_resolve_drawing_number(**kwargs):
        page = kwargs["page"]
        return (f"E-02{page}", "vision")

    monkeypatch.setattr("extractors.raster_extractor.resolve_drawing_number", fake_resolve_drawing_number)

    result = extract_raster_pdf(
        pdf_path=input_pdf,
        out_csv=out_csv,
        debug_dir=debug_dir,
        vision_service_account_json='{"type":"service_account"}',
        page=0,
        dpi=300,
        y_cluster=20.0,
    )

    with out_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert result["pages_processed"] == 2
    assert result["target_pages"] == [1, 2]
    assert len(rows) == 4
    assert rows[0]["図面番号"] == "E-021"
    assert rows[1]["図面番号"] == "E-021"
    assert rows[2]["図面番号"] == "E-022"
    assert rows[3]["図面番号"] == "E-022"


def test_extract_raster_pdf_falls_back_to_legacy_when_v3_returns_no_rows(tmp_path, monkeypatch):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    out_csv = tmp_path / "raster.csv"
    debug_dir = tmp_path / "debug"
    png_path = tmp_path / "dummy.png"
    Image.new("RGB", (32, 32), color=(255, 255, 255)).save(png_path)

    monkeypatch.setattr("extractors.raster_extractor.build_vision_client", lambda _: object())
    monkeypatch.setattr("extractors.raster_extractor.count_pdf_pages", lambda _: 1)
    monkeypatch.setattr("extractors.raster_extractor.run_pdftoppm", lambda *args, **kwargs: png_path)
    monkeypatch.setattr(
        "extractors.raster_extractor.extract_page_rows_v3",
        lambda **kwargs: {"rows": [], "page_words": [], "headers": [], "candidates": [], "tables": []},
    )
    monkeypatch.setattr(
        "extractors.raster_extractor.legacy_side_split_extract_page",
        lambda **kwargs: {
            "rows": [
                {
                    "row_index": 1,
                    "row_y": 120.0,
                    "side": "L",
                    "機器番号": "EF-1",
                    "機器名称": "排風機",
                    "電圧(V)": "200",
                    "容量(kW)": "0.75",
                }
            ],
            "right_side_words": [],
            "right_side_size": (32, 32),
        },
    )
    monkeypatch.setattr(
        "extractors.raster_extractor.resolve_drawing_number",
        lambda **kwargs: ("E-999", "vision"),
    )

    result = extract_raster_pdf(
        pdf_path=input_pdf,
        out_csv=out_csv,
        debug_dir=debug_dir,
        vision_service_account_json='{"type":"service_account"}',
        page=0,
        dpi=300,
        y_cluster=20.0,
    )

    with out_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["図面番号"] == "E-999"
    assert result["fallback_pages"] == []
    assert result["rows_by_page"] == {1: 1}


def test_normalize_row_cells_keeps_point_zero_and_fixes_over_precision_power():
    pac_row = normalize_row_cells(
        {"機器番号": "PAC-15", "機器名称": "空調室外機", "電圧(V)": "200", "容量(kW)": "9.0"}
    )
    ef_row = normalize_row_cells(
        {"機器番号": "EF-R-2", "機器名称": "排風機", "電圧(V)": "200", "容量(kW)": "0.75255"}
    )

    assert pac_row["容量(kW)"] == "9.0"
    assert ef_row["容量(kW)"] == "0.75"


def test_normalize_row_cells_strips_three_phase_voltage_and_capacity_tail():
    row = normalize_row_cells(
        {"機器番号": "PAC-15", "機器名称": "空調室外機", "電圧(V)": "3Φ200V", "容量(kW)": "9.0CVT22E8"}
    )
    assert row["電圧(V)"] == "200"
    assert row["容量(kW)"] == "9.0"


def test_extract_raster_pdf_uses_legacy_first_for_page_one(tmp_path, monkeypatch):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    out_csv = tmp_path / "raster.csv"
    debug_dir = tmp_path / "debug"
    png_path = tmp_path / "dummy.png"
    Image.new("RGB", (40, 40), color=(255, 255, 255)).save(png_path)

    monkeypatch.setattr("extractors.raster_extractor.build_vision_client", lambda _: object())
    monkeypatch.setattr("extractors.raster_extractor.count_pdf_pages", lambda _: 1)
    monkeypatch.setattr("extractors.raster_extractor.run_pdftoppm", lambda *args, **kwargs: png_path)
    monkeypatch.setattr(
        "extractors.raster_extractor.extract_page_rows_v3",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("v3 should not be called for page 1")),
    )
    monkeypatch.setattr(
        "extractors.raster_extractor.legacy_side_split_extract_page",
        lambda **kwargs: {
            "rows": [
                {
                    "row_index": 1,
                    "row_y": 50.0,
                    "side": "L",
                    "機器番号": "EF-1",
                    "機器名称": "排風機",
                    "電圧(V)": "200",
                    "容量(kW)": "0.75",
                }
            ],
            "right_side_words": [],
            "right_side_size": (40, 40),
        },
    )
    monkeypatch.setattr(
        "extractors.raster_extractor.resolve_drawing_number",
        lambda **kwargs: ("E-024", "vision"),
    )

    result = extract_raster_pdf(
        pdf_path=input_pdf,
        out_csv=out_csv,
        debug_dir=debug_dir,
        vision_service_account_json='{"type":"service_account"}',
        page=1,
        dpi=300,
        y_cluster=20.0,
    )

    with out_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert result["rows_by_page"] == {1: 1}
