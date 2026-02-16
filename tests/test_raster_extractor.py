from pathlib import Path

from extractors.raster_extractor import (
    WordBox,
    extract_drawing_number_from_word_boxes,
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
