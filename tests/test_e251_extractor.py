# ruff: noqa: RUF001

from extractors.e251_extractor import (
    EquipmentAnchor,
    RowCluster,
    WordBox,
    _assign_equipment_from_anchors,
    _detect_anchors,
    _extract_candidates_from_cluster,
    build_output_rows,
)


def _word(text: str, cx: float, cy: float = 100.0) -> WordBox:
    return WordBox(text=text, cx=cx, cy=cy, bbox=(cx - 5.0, cy - 5.0, cx + 5.0, cy + 5.0))


def test_extract_candidates_from_cluster_maker_space_model():
    cluster = RowCluster(
        row_y=200.0,
        words=[
            _word("DAIKO", 100.0, cy=200.0),
            _word("LZD-93195XW", 220.0, cy=200.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert rows == [
        {
            "器具記号": "",
            "メーカー": "DAIKO",
            "相当型番": "LZD-93195XW",
            "row_x": 95.0,
        }
    ]


def test_extract_candidates_from_cluster_maker_colon_model():
    cluster = RowCluster(
        row_y=220.0,
        words=[
            _word("DNL", 100.0, cy=220.0),
            _word(":", 135.0, cy=220.0),
            _word("D-EX12", 180.0, cy=220.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert rows == [
        {
            "器具記号": "",
            "メーカー": "DNL",
            "相当型番": "D-EX12",
            "row_x": 95.0,
        }
    ]


def test_extract_candidates_from_cluster_eq_colon_maker_model():
    cluster = RowCluster(
        row_y=240.0,
        words=[
            _word("L1", 100.0, cy=240.0),
            _word("(L1500)", 155.0, cy=240.0),
            _word(":", 215.0, cy=240.0),
            _word("DAIKO", 275.0, cy=240.0),
            _word("DSY-4394YWG", 410.0, cy=240.0),
        ],
    )
    rows = _extract_candidates_from_cluster(cluster)
    assert rows == [
        {
            "器具記号": "L1",
            "メーカー": "DAIKO",
            "相当型番": "DSY-4394YWG",
            "row_x": 95.0,
        }
    ]


def test_detect_anchors_keeps_d_and_l_codes_and_blanks_symbolic_markers():
    clusters = [
        RowCluster(
            row_y=80.0,
            words=[
                _word("D1", 100.0, cy=80.0),
                _word("D2", 300.0, cy=80.0),
                _word("L1", 500.0, cy=80.0),
                _word("H", 700.0, cy=80.0),
            ],
        )
    ]

    anchors = _detect_anchors(clusters, title_y=70.0)

    assert [(anchor.raw, anchor.equipment) for anchor in anchors] == [
        ("D1", "D1"),
        ("D2", "D2"),
        ("L1", "L1"),
        ("H", ""),
    ]


def test_assign_equipment_from_anchors_uses_nearest_anchor_and_blanks_symbol_rows():
    candidates = [
        {"器具記号": "", "メーカー": "DAIKO", "相当型番": "LZD-93195XW", "row_x": 110.0},
        {"器具記号": "", "メーカー": "Panasonic", "相当型番": "WTF4088CWK", "row_x": 720.0},
    ]
    anchors = [
        EquipmentAnchor(x=100.0, raw="D1", equipment="D1"),
        EquipmentAnchor(x=700.0, raw="H", equipment=""),
    ]

    _assign_equipment_from_anchors(candidates, anchors=anchors)

    assert candidates[0]["器具記号"] == "D1"
    assert candidates[1]["器具記号"] == ""


def test_build_output_rows_keeps_d1_and_l1_and_skips_blank_rows():
    rows = build_output_rows(
        [
            {"page": 1, "row_y": 300.0, "row_x": 120.0, "器具記号": "", "メーカー": "", "相当型番": ""},
            {"page": 1, "row_y": 220.0, "row_x": 200.0, "器具記号": "L1", "メーカー": "DAIKO", "相当型番": "DSY-4394YWG"},
            {"page": 1, "row_y": 200.0, "row_x": 100.0, "器具記号": "D1", "メーカー": "DAIKO", "相当型番": "LZD-93195XW"},
        ]
    )

    assert rows == [
        {"器具記号": "D1", "メーカー": "DAIKO", "相当型番": "LZD-93195XW"},
        {"器具記号": "L1", "メーカー": "DAIKO", "相当型番": "DSY-4394YWG"},
    ]
