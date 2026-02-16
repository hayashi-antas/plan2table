import csv
from pathlib import Path

from PIL import Image

from extractors.raster_extractor import (
    ColumnBounds,
    WordBox,
    extract_raster_pdf,
    extract_drawing_number_from_word_boxes,
    normalize_row_cells,
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


def test_normalize_row_cells_keeps_point_zero_and_fixes_over_precision_power():
    pac_row = normalize_row_cells(
        {"機器番号": "PAC-15", "機器名称": "空調室外機", "電圧(V)": "200", "容量(kW)": "9.0"}
    )
    ef_row = normalize_row_cells(
        {"機器番号": "EF-R-2", "機器名称": "排風機", "電圧(V)": "200", "容量(kW)": "0.75255"}
    )

    assert pac_row["容量(kW)"] == "9.0"
    assert ef_row["容量(kW)"] == "0.75"
