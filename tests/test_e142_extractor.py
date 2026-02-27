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


def test_extract_label_value_pairs_supports_output_voltage_and_current_in_same_segment():
    pairs = extract_label_value_pairs("出力電圧DC24V出力電流1A")
    assert pairs == [("出力電圧", "DC24V"), ("出力電流", "1A")]


def test_extract_label_value_pairs_merges_duplicate_non_empty_labels():
    pairs = extract_label_value_pairs("備考注意備考要確認")
    assert pairs == [("備考", "注意 要確認")]


def test_extract_label_value_pairs_supports_toso_label():
    pairs = extract_label_value_pairs("塗装黒電着塗装")
    assert pairs == [("塗装", "黒電着塗装")]


def test_extract_label_value_pairs_normalizes_black_variant():
    pairs = extract_label_value_pairs("塗装黑電着塗装")
    assert pairs == [("塗装", "黒電着塗装")]


def test_extract_label_value_pairs_normalizes_mass_label_ocr_noise():
    pairs = extract_label_value_pairs("質★15kg")
    assert pairs == [("質量", "15kg")]


def test_extract_label_value_pairs_does_not_inject_mass_into_material_value():
    pairs = extract_label_value_pairs("材質本体:自己消火性樹脂/パネル:ステンレス(シルバー)")
    assert pairs == [("材質", "本体:自己消火性樹脂/パネル:ステンレス(シルバー)")]


def test_extract_label_value_pairs_keeps_mass_row_fix_when_line_starts_with_shitsu_hontai():
    pairs = extract_label_value_pairs("質本体約5.4Kg")
    assert pairs == [("質量", "本体約5.4Kg")]


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


def test_build_frame_rows_supports_split_mass_label_and_value_row():
    rows = build_frame_rows_from_segments(
        [
            _segment("壁取付具", y=100.0, x0=120.0, x1=360.0),
            _segment("MN-T2170", y=140.0, x0=470.0, x1=620.0),
            _segment("質", y=300.0, x0=100.0, x1=140.0),
            _segment("約36g", y=300.0, x0=220.0, x1=320.0),
            _segment("材質 自己消火性PC+ABS樹脂(UL94V-0)", y=340.0, x0=100.0, x1=760.0),
            _segment("形状 屋外壁面露出設置", y=380.0, x0=100.0, x1=760.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert values[0] == "壁取付具"
    assert values[1] == "MN-T2170"
    assert "質量" in values
    assert "約36g" in values
    assert "材質" in values
    assert "形状" in values


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


def test_build_frame_rows_layout_fallback_extracts_unknown_label_pairs():
    rows = build_frame_rows_from_segments(
        [
            _segment("環境センサー", y=100.0, x0=140.0, x1=320.0),
            _segment("ES-X1234", y=140.0, x0=470.0, x1=620.0),
            _segment("使用温度範囲", y=300.0, x0=100.0, x1=300.0),
            _segment("0〜40℃", y=300.0, x0=380.0, x1=520.0),
            _segment("保護等級", y=340.0, x0=100.0, x1=260.0),
            _segment("IP54", y=340.0, x0=380.0, x1=460.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert values[0] == "環境センサー"
    assert values[1] == "ES-X1234"
    assert "使用温度範囲" in values
    assert "0〜40℃" in values
    assert "保護等級" in values
    assert "IP54" in values


def test_build_frame_rows_title_only_fallback_ignores_unrelated_distant_code():
    rows = build_frame_rows_from_segments(
        [
            _segment("住戸モニターアダプター", y=100.0, x0=150.0, x1=430.0),
            _segment("MS-D1220", y=140.0, x0=2200.0, x1=2340.0),
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
    assert rows[0].values[1] == "MC-N0190"
    assert rows[1].values[1] == "KB-X0670"
    assert rows[2].values[1] == "AC-A0480"


def test_build_frame_rows_prefers_code_with_better_x_overlap_in_neighbor_frames():
    rows = build_frame_rows_from_segments(
        [
            _segment("メインコントローラ", y=100.0, x0=120.0, x1=360.0),
            _segment("MC-N0190", y=140.0, x0=520.0, x1=660.0),
            _segment("電源電圧 AC100V", y=320.0, x0=100.0, x1=640.0),
            _segment("形状 露出型", y=360.0, x0=100.0, x1=640.0),
            _segment("鍵管理ボックス(120戸)", y=108.0, x0=760.0, x1=1120.0),
            _segment("KB-X0670", y=140.0, x0=1120.0, x1=1260.0),
            _segment("材質 メラミン樹脂焼付塗装", y=322.0, x0=760.0, x1=1040.0),
            _segment("塗色 ライトグレー", y=352.0, x0=760.0, x1=1020.0),
            _segment("形状 露出型", y=382.0, x0=760.0, x1=1000.0),
        ]
    )

    row_by_title = {row.values[0]: row.values for row in rows}
    assert row_by_title["メインコントローラ"][1] == "MC-N0190"
    assert row_by_title["鍵管理ボックス（120戸）"][1] == "KB-X0670"  # noqa: RUF001  # intentional fullwidth parentheses


def test_build_frame_rows_keeps_high_overlap_code_when_score_is_slightly_above_threshold():
    rows = build_frame_rows_from_segments(
        [
            _segment("PS10電源アダプター", y=182.0, x0=1339.0, x1=1665.0),
            _segment("KB-X0670", y=229.0, x0=1152.0, x1=1292.0),
            _segment("AC-A0480", y=229.0, x0=1746.0, x1=1889.0),
            _segment("電源電圧", y=621.0, x0=1373.0, x1=1633.0),
            _segment("AC100V", y=621.0, x0=1660.0, x1=1810.0),
            _segment("出力電圧", y=651.0, x0=1373.0, x1=1633.0),
            _segment("DC24V", y=651.0, x0=1660.0, x1=1810.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "電源アダプター"
    assert rows[0].values[1] == "AC-A0480"


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


def test_build_frame_rows_preserves_toku_suffix_in_product_code():
    rows = build_frame_rows_from_segments(
        [
            _segment("カメラ付ロビーインターホン", y=100.0, x0=120.0, x1=460.0),
            _segment("MS-L1370トク", y=140.0, x0=500.0, x1=700.0),
            _segment("電源電圧 DC24V", y=300.0, x0=100.0, x1=760.0),
            _segment("質量 約3.0kg", y=340.0, x0=100.0, x1=760.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "カメラ付ロビーインターホン"
    assert rows[0].values[1] == "MS-L1370トク"


def test_build_frame_rows_supports_single_char_suffix_code_like_rs_a():
    rows = build_frame_rows_from_segments(
        [
            _segment("漏水センサー", y=100.0, x0=120.0, x1=340.0),
            _segment("RS-A", y=140.0, x0=500.0, x1=620.0),
            _segment("電源電圧 ACまたはDC5~24V", y=300.0, x0=100.0, x1=760.0),
            _segment("形状 床面設置形", y=340.0, x0=100.0, x1=760.0),
            _segment("備考 センサ点検口必要", y=380.0, x0=100.0, x1=760.0),
        ]
    )

    assert len(rows) == 1
    assert rows[0].values[0] == "漏水センサー"
    assert rows[0].values[1] == "RS-A"


def test_build_frame_rows_uses_code_segments_to_avoid_neighbor_code_misattribution():
    rows = build_frame_rows_from_segments(
        [
            _segment(
                "1|高感度用マグネットセンサー(埋込型)セキュリティインターホン親機専用埋込ボックス(横型)",
                y=1953.0,
                x0=2529.0,
                x1=3630.0,
            ),
            _segment("材質 ABS樹脂", y=2464.0, x0=2554.0, x1=2884.0),
            _segment("形状 埋込型(専用取付金具が必要)", y=2505.0, x0=2554.0, x1=2884.0),
            _segment("材質 硬質PVC", y=2416.0, x0=3143.0, x1=3408.0),
            _segment("色調 黒", y=2451.0, x0=3143.0, x1=3408.0),
            _segment("質量 約650g", y=2479.0, x0=3143.0, x1=3408.0),
            _segment("備考 RC固定用補助材付属", y=2506.0, x0=3143.0, x1=3408.0),
            _segment("MG-T0320コンクリート用", y=2000.0, x0=2924.0, x1=3214.0),
        ],
        title_segments=[
            _segment(
                "1|高感度用マグネットセンサー(埋込型)セキュリティインターホン親機専用埋込ボックス(横型)",
                y=1953.0,
                x0=2529.0,
                x1=3630.0,
            )
        ],
        code_segments=[
            _segment("MG-T0320", y=2000.0, x0=2924.0, x1=3064.0),
            _segment("コンクリート用", y=2000.0, x0=3093.0, x1=3214.0),
        ],
    )

    row_by_title = {row.values[0]: row.values for row in rows}
    assert row_by_title["高感度用マグネットセンサー（埋込型）"][1] == "MG-T0320"  # noqa: RUF001  # intentional fullwidth parentheses
    assert row_by_title["セキュリティインターホン親機専用埋込ボックス（横型）"][1] == "材質"  # noqa: RUF001  # intentional fullwidth parentheses


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


def test_build_frame_rows_keeps_multiline_biko_continuations():
    rows = build_frame_rows_from_segments(
        [
            _segment("カメラ付ロビーインターホン", y=100.0, x0=120.0, x1=500.0),
            _segment("MS-L1370トク", y=140.0, x0=520.0, x1=700.0),
            _segment("電源電圧 DC24V", y=300.0, x0=100.0, x1=760.0),
            _segment("質量 約3.0kg", y=340.0, x0=100.0, x1=760.0),
            _segment("備考 防まつ形（JIS C 0920 IPX4 相当）", y=380.0, x0=100.0, x1=760.0),
            _segment("ただし、直射日光や強い紫外線の当たる場所への設置は避けること", y=420.0, x0=210.0, x1=760.0),
            _segment("トク・カラーSUSパネル（選択色制限あり）", y=460.0, x0=210.0, x1=760.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    assert "備考" in values
    biko_value = values[values.index("備考") + 1]
    assert "防まつ形" in biko_value
    assert "ただし、直射日光や強い紫外線の当たる場所への設置は避けること" in biko_value
    assert "トク・カラーSUSパネル（選択色制限あり）" in biko_value


def test_build_frame_rows_keeps_multiline_toshoku_with_code_prefixed_lines():
    rows = build_frame_rows_from_segments(
        [
            _segment("マグネットセンサー(露出型)", y=100.0, x0=120.0, x1=460.0),
            _segment("MG-TO130", y=140.0, x0=500.0, x1=700.0),
            _segment("質量 スイッチ:10g マグネット:9g", y=300.0, x0=100.0, x1=760.0),
            _segment("材質 スイッチ:ABS樹脂 マグネット:鉄・コバルト・ニッケル合金", y=340.0, x0=100.0, x1=760.0),
            _segment("塗色 SPM-0090:グレー MG-T0060:アイボリー", y=380.0, x0=100.0, x1=760.0),
            _segment("(スイッチ) MG-T0070:ライトグレー MG-T0080:ブラウン", y=420.0, x0=170.0, x1=760.0),
            _segment("MG-T0130:ブラック", y=460.0, x0=220.0, x1=520.0),
            _segment("塗色 SPM-0092:メタリックシルバー", y=500.0, x0=100.0, x1=760.0),
            _segment("(マグネット) その他は、スイッチと同色", y=540.0, x0=170.0, x1=760.0),
            _segment("形状 露出型", y=580.0, x0=100.0, x1=760.0),
        ]
    )

    assert len(rows) == 1
    values = rows[0].values
    first_toshoku_idx = values.index("塗色")
    first_toshoku_value = values[first_toshoku_idx + 1]
    assert "SPM-0090:グレー" in first_toshoku_value
    assert "MG-T0060:アイボリー" in first_toshoku_value
    assert "MG-T0070:ライトグレー" in first_toshoku_value
    assert "MG-T0080:ブラウン" in first_toshoku_value
    assert "MG-T0130:ブラック" in first_toshoku_value

    second_toshoku_idx = values.index("塗色", first_toshoku_idx + 1)
    second_toshoku_value = values[second_toshoku_idx + 1]
    assert "SPM-0092:メタリックシルバー" in second_toshoku_value
    assert "その他は、スイッチと同色" in second_toshoku_value


def test_build_frame_rows_does_not_append_next_title_to_toshoku_value():
    rows = build_frame_rows_from_segments(
        [
            _segment("マグネットセンサー", y=100.0, x0=120.0, x1=360.0),
            _segment("SPM-0070", y=140.0, x0=470.0, x1=620.0),
            _segment("質量 スイッチ:27g マグネット:28g", y=300.0, x0=100.0, x1=760.0),
            _segment("材質 ABS樹脂", y=340.0, x0=100.0, x1=760.0),
            _segment("塗色 ライトグレー", y=380.0, x0=100.0, x1=520.0),
            _segment("8インフラレッドセンサー", y=420.0, x0=100.0, x1=420.0),
        ]
    )

    assert len(rows) >= 1
    target = rows[0].values
    assert "塗色" in target
    toshoku = target[target.index("塗色") + 1]
    assert "ライトグレー" in toshoku
    assert "インフラレッドセンサー" not in toshoku


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
