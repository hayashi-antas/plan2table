from extractors.e142_extractor import (
    FrameRow,
    _refine_titles_for_reference_rows,
    Segment,
    build_frame_rows_from_segments,
    extract_label_value_pairs,
)


def _segment(
    text: str,
    *,
    y: float,
    x0: float,
    x1: float,
    page: int = 1,
) -> Segment:
    compact = text.replace(" ", "").replace("　", "")
    return Segment(
        page=page,
        row_y=y,
        x0=x0,
        x1=x1,
        top=y - 6.0,
        bottom=y + 6.0,
        text=text,
        text_compact=compact,
    )


def test_extract_label_value_pairs_supports_multiple_labels_in_one_segment():
    pairs = extract_label_value_pairs("電源電圧AC100V消費電流0.8A以下")
    assert pairs == [("電源電圧", "AC100V"), ("消費電流", "0.8A以下")]


def test_extract_label_value_pairs_supports_toso_label():
    pairs = extract_label_value_pairs("塗装黒電着塗装")
    assert pairs == [("塗装", "黒電着塗装")]


def test_extract_label_value_pairs_normalizes_black_variant():
    pairs = extract_label_value_pairs("塗装黑電着塗装")
    assert pairs == [("塗装", "黒電着塗装")]


def test_build_frame_rows_extracts_title_code_and_label_value_pairs():
    rows = build_frame_rows_from_segments(
        [
            _segment("メイン コントローラ", y=100.0, x0=120.0, x1=340.0),
            _segment("MC-N0190", y=140.0, x0=470.0, x1=620.0),
            _segment("電源電圧 AC100V", y=230.0, x0=100.0, x1=640.0),
            _segment("消費電流 0.8A以下", y=260.0, x0=100.0, x1=640.0),
            _segment("質量 本体約5.4Kg", y=290.0, x0=100.0, x1=640.0),
            _segment("回線ユニット約120g", y=315.0, x0=100.0, x1=640.0),
            _segment("材質 鋼板1.2t", y=340.0, x0=100.0, x1=640.0),
            _segment("形状 露出型", y=370.0, x0=100.0, x1=640.0),
            _segment("備考 公衆回線用回線ユニット内蔵", y=400.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert values[0] == "メインコントローラ"
    assert values[1] == "MC-N0190"
    assert "電源電圧" in values
    assert "AC100V" in values
    assert "消費電流" in values
    assert "0.8A以下" in values
    assert "質量" in values
    assert any("本体約5.4Kg" in value for value in values)
    assert "材質" in values
    assert "形状" in values
    assert "備考" in values


def test_build_frame_rows_handles_ocr_label_noise_and_multiline_mass():
    rows = build_frame_rows_from_segments(
        [
            _segment("メイン コントローラ", y=100.0, x0=120.0, x1=340.0),
            _segment("MC-N0190", y=140.0, x0=470.0, x1=620.0),
            _segment("電電源電圧 AC100V", y=230.0, x0=100.0, x1=640.0),
            _segment("消消費電流 0.8A以下", y=260.0, x0=100.0, x1=640.0),
            _segment("質本体約5.4Kg+取付板約1.2Kg+公衆回線用", y=290.0, x0=100.0, x1=640.0),
            _segment("回線ユニット約120g（バッテリー含まず）", y=315.0, x0=200.0, x1=640.0),
            _segment("材質 鋼板1.2t", y=340.0, x0=100.0, x1=640.0),
            _segment("形備状 露出型", y=370.0, x0=100.0, x1=640.0),
            _segment("考 公衆回線用回線ユニット内蔵", y=400.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert values[0] == "メインコントローラ"
    assert values[1] == "MC-N0190"
    assert "質量" in values
    assert any("回線ユニット約120g" in value for value in values)
    assert "形状" in values
    assert "備考" in values


def test_build_frame_rows_without_table_returns_title_and_code():
    rows = build_frame_rows_from_segments(
        [
            _segment("漏水センサー", y=100.0, x0=150.0, x1=340.0),
            _segment("MS-D1220", y=140.0, x0=420.0, x1=560.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values == ["漏水センサー", "MS-D1220"]


def test_build_frame_rows_without_code_returns_title_only():
    rows = build_frame_rows_from_segments(
        [
            _segment("住戸モニターアダプター", y=100.0, x0=150.0, x1=430.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values == ["住戸モニターアダプター"]


def test_build_frame_rows_does_not_adopt_symbol_cell_as_title():
    rows = build_frame_rows_from_segments(
        [
            _segment("□", y=100.0, x0=80.0, x1=110.0),
            _segment("メインコントローラ", y=102.0, x0=140.0, x1=360.0),
            _segment("MC-N0190", y=140.0, x0=470.0, x1=620.0),
            _segment("電源電圧 AC100V", y=230.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "メインコントローラ"


def test_build_frame_rows_orders_left_to_right_then_next_row():
    rows = build_frame_rows_from_segments(
        [
            _segment("メインコントローラ", y=100.0, x0=140.0, x1=360.0),
            _segment("MC-N0190", y=150.0, x0=470.0, x1=620.0),
            _segment("電源電圧 AC100V", y=320.0, x0=100.0, x1=620.0),
            _segment("形状 露出型", y=360.0, x0=100.0, x1=620.0),
            _segment("鍵管理ボックス(120戸)", y=108.0, x0=780.0, x1=1060.0),
            _segment("KB-X0670", y=150.0, x0=1180.0, x1=1320.0),
            _segment("質量 15kg", y=322.0, x0=760.0, x1=1360.0),
            _segment("材質 メラミン樹脂焼付塗装", y=362.0, x0=760.0, x1=1360.0),
            _segment("電源アダプター", y=980.0, x0=140.0, x1=360.0),
            _segment("AC-A0480", y=1025.0, x0=470.0, x1=620.0),
            _segment("電源電圧 AC100V", y=1220.0, x0=100.0, x1=620.0),
            _segment("出力電圧 DC24V", y=1260.0, x0=100.0, x1=620.0),
        ]
    )

    assert [row.values[0] for row in rows] == [  # noqa: RUF001  # intentional fullwidth parentheses
        "メインコントローラ",
        "鍵管理ボックス（120戸）",  # noqa: RUF001  # intentional fullwidth parentheses
        "電源アダプター",
    ]


def test_build_frame_rows_skips_outlier_large_frame_when_same_size_frames_exist():
    rows = build_frame_rows_from_segments(
        [
            _segment("メインコントローラ", y=100.0, x0=140.0, x1=360.0),
            _segment("MC-N0190", y=150.0, x0=470.0, x1=620.0),
            _segment("電源電圧 AC100V", y=320.0, x0=100.0, x1=620.0),
            _segment("形状 露出型", y=360.0, x0=100.0, x1=620.0),
            _segment("鍵管理ボックス(120戸)", y=108.0, x0=780.0, x1=1060.0),
            _segment("KB-X0670", y=150.0, x0=1180.0, x1=1320.0),
            _segment("質量 15kg", y=322.0, x0=760.0, x1=1360.0),
            _segment("材質 メラミン樹脂焼付塗装", y=362.0, x0=760.0, x1=1360.0),
            _segment("右側大枠", y=105.0, x0=1560.0, x1=1860.0),
            _segment("RG-X9999", y=155.0, x0=1960.0, x1=2120.0),
            _segment("電源電圧 AC200V", y=330.0, x0=1480.0, x1=2880.0),
            _segment("消費電流 1.2A以下", y=370.0, x0=1480.0, x1=2880.0),
        ]
    )

    titles = [row.values[0] for row in rows]
    assert "メインコントローラ" in titles
    assert "鍵管理ボックス（120戸）" in titles  # noqa: RUF001  # intentional fullwidth parentheses
    assert "右側大枠" not in titles


def test_build_frame_rows_normalizes_title_header_marker_prefix():
    rows = build_frame_rows_from_segments(
        [
            _segment("KB120鍵管理ボックス(120戸)", y=100.0, x0=120.0, x1=420.0),
            _segment("KB-X0670", y=140.0, x0=480.0, x1=620.0),
            _segment("質量 15kg", y=300.0, x0=100.0, x1=640.0),
            _segment("材質 メラミン樹脂焼付塗装", y=340.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "鍵管理ボックス（120戸）"  # noqa: RUF001  # intentional fullwidth parentheses


def test_build_frame_rows_does_not_assign_far_code_candidate():
    rows = build_frame_rows_from_segments(
        [
            _segment("漏水センサー", y=100.0, x0=100.0, x1=320.0),
            _segment("MS-D1220", y=140.0, x0=2000.0, x1=2140.0),
            _segment("電源電圧 ACまたはDC5~24V", y=300.0, x0=100.0, x1=640.0),
            _segment("形状 床面設置形", y=340.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "漏水センサー"
    assert "MS-D1220" not in rows[0].values


def test_build_frame_rows_supports_tokuchuhin_as_identifier_column():
    rows = build_frame_rows_from_segments(
        [
            _segment("2方向アダプター", y=100.0, x0=120.0, x1=420.0),
            _segment("特注品", y=140.0, x0=500.0, x1=620.0),
            _segment("電源電圧 AC100V 50/60Hz", y=300.0, x0=100.0, x1=640.0),
            _segment("材質 自己消火性ABS樹脂", y=340.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "2方向アダプター"
    assert rows[0].values[1] == "特注品"


def test_build_frame_rows_supports_product_code_identifier_and_paint_row():
    rows = build_frame_rows_from_segments(
        [
            _segment("ロビーインターホン用埋込ボックス", y=100.0, x0=120.0, x1=540.0),
            _segment("(商品コード:4361000)", y=150.0, x0=420.0, x1=640.0),
            _segment("材質 鋼板", y=320.0, x0=100.0, x1=640.0),
            _segment("塗装 黒電着塗装", y=360.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert values[0] == "ロビーインターホン用埋込ボックス"
    assert values[1] == "(商品コード:4361000)"
    assert "材質" in values
    assert "鋼板" in values
    assert "塗装" in values
    assert "黒電着塗装" in values


def test_build_frame_rows_accepts_product_code_when_distance_is_slightly_large():
    rows = build_frame_rows_from_segments(
        [
            _segment("ロビーインターホン用埋込ボックス", y=100.0, x0=120.0, x1=540.0),
            _segment("商品コード:4361000", y=150.0, x0=730.0, x1=860.0),
            _segment("材質 鋼板", y=320.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "ロビーインターホン用埋込ボックス"
    assert rows[0].values[1] == "商品コード:4361000"


def test_build_frame_rows_prefers_parenthesized_product_code():
    rows = build_frame_rows_from_segments(
        [
            _segment("ロビーインターホン用埋込ボックス", y=100.0, x0=120.0, x1=540.0),
            _segment("(商品コード:4361000)", y=150.0, x0=420.0, x1=640.0),
            _segment("材質 鋼板", y=320.0, x0=100.0, x1=640.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[1] == "(商品コード:4361000)"


def test_build_frame_rows_reference_example_is_title_only():
    rows = [
        FrameRow(page=1, top=100.0, x0=100.0, title="マグネットセンサー(露出型)", code="", pairs=[]),
        FrameRow(
            page=1,
            top=110.0,
            x0=520.0,
            title="8φ通線孔(建築工事)",
            code="MS-X0001",
            pairs=[("形状", "により取付が異なる場合があります。")],
        ),
    ]

    _refine_titles_for_reference_rows(rows)

    assert rows[1].values == ["マグネットセンサー（露出型）取付参考例"]  # noqa: RUF001  # intentional fullwidth parentheses
