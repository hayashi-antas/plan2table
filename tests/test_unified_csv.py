import csv
from pathlib import Path

from extractors.unified_csv import merge_vector_raster_csv


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_merge_outputs_fixed_judgment_columns_and_bom(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "\n".join(
            [
                "機械番号,名称,動力(50Hz)_消費電力(Kw),台数,図面番号",
                "A-1,排風機,1.5,2,M-101",
                "B-1,送風機,2.0,1,M-102",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧（V）,容量(KW),図面番号",
                "A-1,送風機,200,1.5,E-024",
                "A-1,送風機,200,2.0,E-031",
                "A-1,予備,100,1.5,E-024",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )
    assert result["rows"] == 2
    assert out_csv.exists()
    assert out_csv.read_bytes().startswith(b"\xef\xbb\xbf")

    rows = _read_rows(out_csv)
    assert len(rows) == 2

    header = list(rows[0].keys())
    assert header[:6] == ["総合判定", "台数判定", "容量判定", "名称判定", "機器ID照合", "判定理由"]
    assert "照合結果" not in header
    assert "不一致内容" not in header
    assert "確認理由" not in header
    assert "名称差異" not in header
    assert "機器表 モード容量(kW)" in header
    assert "機器表 判定モード" in header
    assert "機器表 判定採用容量(kW)" in header
    assert "容量判定補足" in header
    assert "容量判定理由コード" not in header

    a1 = rows[0]
    assert a1["総合判定"] == "要確認"
    assert a1["台数判定"] == "✗"
    assert a1["容量判定"] == "要確認"
    assert a1["名称判定"] == "✗"
    assert a1["機器ID照合"] == "◯"
    assert a1["判定理由"] == "容量が複数候補"
    assert a1["機器ID"] == "A-1"
    assert a1["盤表 記載名"] == "送風機,予備"
    assert a1["盤表 容量(kW)"] == "1.5,2"
    assert a1["台数差"] == "1"
    assert a1["容量差(kW)"] == ""
    assert a1["機器表 判定モード"] == "単一値"
    assert a1["機器表 判定採用容量(kW)"] == "1.5"
    assert a1["機器表 図面番号"] == "M-101"
    assert a1["盤表 図面番号"] == "E-024,E-031"
    assert (
        a1["盤表 記載トレース"]
        == "図面:E-024 名称:送風機 容量:1.5 || "
        "図面:E-031 名称:送風機 容量:2.0 || "
        "図面:E-024 名称:予備 容量:1.5"
    )

    b1 = rows[1]
    assert b1["総合判定"] == "✗"
    assert b1["台数判定"] == "✗"
    assert b1["容量判定"] == "✗"
    assert b1["名称判定"] == "✗"
    assert b1["機器ID照合"] == "✗"
    assert b1["判定理由"] == "盤表に記載なし"
    assert b1["機器ID"] == "B-1"
    assert b1["盤表 台数"] == "0"
    assert b1["機器表 判定モード"] == "単一値"
    assert b1["機器表 判定採用容量(kW)"] == "2"
    assert b1["盤表 記載トレース"] == ""


def test_merge_uses_max_capacity_when_mode_is_unknown(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nPAC-1,空調室外機,(冷)9.45 / (暖)7.18,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nPAC-1,空調室外機,200,9.45\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )
    row = _read_rows(out_csv)[0]
    assert row["総合判定"] == "◯"
    assert row["台数判定"] == "◯"
    assert row["容量判定"] == "◯"
    assert row["名称判定"] == "◯"
    assert row["判定理由"] == ""
    assert row["機器表 消費電力(kW)"] == "(冷)9.45 / (暖)7.18"
    assert row["機器表 モード容量(kW)"] == "冷=9.45,暖=7.18"
    assert row["機器表 判定モード"] == "最大値(冷)"
    assert row["機器表 判定採用容量(kW)"] == "9.45"
    assert row["容量判定補足"] == "機器名称からモード特定不可のため最大値を採用"


def test_merge_keeps_review_when_unknown_mode_has_tied_max_values(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        (
            "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\n"
            "PAC-1,空調室外機,(冷)9.45 / (暖)9.45 / (低温)8.0,1\n"
        ),
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nPAC-1,空調室外機,200,9.45\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )
    row = _read_rows(out_csv)[0]
    assert row["総合判定"] == "要確認"
    assert row["容量判定"] == "要確認"
    assert row["判定理由"] == "容量が数値でない"
    assert row["機器表 判定モード"] == "未確定"
    assert row["機器表 判定採用容量(kW)"] == ""
    assert row["容量判定補足"] == "機器名称からモード特定不可かつ最大値が複数(冷,暖)"


def test_merge_uses_strict_mode_when_env_disables_max_fallback(tmp_path, monkeypatch):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    monkeypatch.setenv("ME_CHECK_CAPACITY_FALLBACK", "strict")

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nPAC-6,空調室外機,(冷)1.52 / (暖)1.66 / (低温)2,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nPAC-6,空調室外機,200,2\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    row = _read_rows(out_csv)[0]
    assert row["容量判定"] == "要確認"
    assert row["機器表 判定モード"] == "未確定"
    assert row["機器表 判定採用容量(kW)"] == ""
    assert row["容量判定補足"] == "機器名称からモード特定不可(strict設定)"


def test_merge_uses_cooling_capacity_when_name_has_cooling_only_hint(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        (
            "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\n"
            "PAC-1,空調機/空冷HPパッケージ/マルチタイプ/(冷房専用),"
            "(冷)9.45 / (暖)7.18 / (低温)9.43,1\n"
        ),
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nPAC-1,空調機/空冷HPパッケージ/マルチタイプ/(冷房専用),200,9.45\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )
    row = _read_rows(out_csv)[0]
    assert row["総合判定"] == "◯"
    assert row["台数判定"] == "◯"
    assert row["容量判定"] == "◯"
    assert row["名称判定"] == "◯"
    assert row["判定理由"] == ""
    assert row["機器表 消費電力(kW)"] == "(冷)9.45 / (暖)7.18 / (低温)9.43"
    assert row["機器表 モード容量(kW)"] == "冷=9.45,暖=7.18,低温=9.43"
    assert row["機器表 判定モード"] == "冷"
    assert row["機器表 判定採用容量(kW)"] == "9.45"
    assert row["容量判定補足"] == "機器名称ヒント(冷房専用)で(冷)を採用"
    assert row["容量差(kW)"] == "0"


def test_merge_uses_heating_capacity_when_name_has_heating_only_hint(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        (
            "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\n"
            "PAC-2,空調機/空冷HPパッケージ/(暖房専用),(冷)3.9 / (暖)4.05 / (低温)5.32,1\n"
        ),
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nPAC-2,空調機/空冷HPパッケージ/(暖房専用),200,4.05\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    row = _read_rows(out_csv)[0]
    assert row["総合判定"] == "◯"
    assert row["容量判定"] == "◯"
    assert row["機器表 判定モード"] == "暖"
    assert row["機器表 判定採用容量(kW)"] == "4.05"
    assert row["容量判定補足"] == "機器名称ヒント(暖房専用)で(暖)を採用"


def test_merge_regression_pac_modes_with_max_fallback(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "\n".join(
            [
                "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数",
                "PAC-1,空調機/空冷HPパッケージ/マルチタイプ/(冷房専用),(冷)9.45 / (暖)7.18 / (低温)9.43,1",
                "PAC-6,空調機/空冷HPパッケージ/マルチタイプ,(冷)1.52 / (暖)1.66 / (低温)2,1",
                "PAC-14,空調機/空冷HPパッケージ/マルチタイプ,(冷)5.11 / (暖)5.46 / (低温)6.3,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧(V),容量(kW)",
                "PAC-1,空調室外機,200,9.45",
                "PAC-6,空調室外機,200,2",
                "PAC-14,空調室外機,200,8.6",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    rows = {row["機器ID"]: row for row in _read_rows(out_csv)}
    assert rows["PAC-1"]["容量判定"] == "◯"
    assert rows["PAC-1"]["機器表 判定モード"] == "冷"

    assert rows["PAC-6"]["容量判定"] == "◯"
    assert rows["PAC-6"]["機器表 判定モード"] == "最大値(低温)"
    assert rows["PAC-6"]["機器表 判定採用容量(kW)"] == "2"

    assert rows["PAC-14"]["容量判定"] == "✗"
    assert rows["PAC-14"]["機器表 判定モード"] == "最大値(低温)"
    assert rows["PAC-14"]["機器表 判定採用容量(kW)"] == "6.3"


def test_merge_appends_raster_only_and_missing_id_rows(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,排風機,1.5,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧(V),容量(kW),図面番号",
                "A-1,排風機,200,1.5,E-024",
                "R-9,還気ファン,200,0.75,E-099",
                ",全自動砂濾過装置,200,2.0,E-024",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    rows = _read_rows(out_csv)
    assert len(rows) == 3

    raster_only = rows[1]
    assert raster_only["機器ID"] == "R-9"
    assert raster_only["総合判定"] == "✗"
    assert raster_only["機器ID照合"] == "✗"
    assert raster_only["判定理由"] == "機器表に記載なし"

    missing_id = rows[2]
    assert missing_id["機器ID"] == ""
    assert missing_id["総合判定"] == "要確認"
    assert missing_id["台数判定"] == "要確認"
    assert missing_id["容量判定"] == "要確認"
    assert missing_id["名称判定"] == "要確認"
    assert missing_id["機器ID照合"] == "✗"
    assert missing_id["判定理由"] == "盤表ID未記載"


def test_merge_trace_compacts_duplicate_rows_with_count(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,排風機,1.5,2\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧(V),容量(kW),図面番号",
                "A-1,送風機,200,1.5,E-024",
                "A-1,送風機,200,1.5,E-024",
                "A-1,送風機,200,1.5,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    row = _read_rows(out_csv)[0]
    assert (
        row["盤表 記載トレース"]
        == "図面:E-024 名称:送風機 容量:1.5 x2 || 図面:? 名称:送風機 容量:1.5"
    )


def test_merge_uses_review_over_mismatch_for_overall_priority(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nEF-R-2,排風機,0.75,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧(V),容量(kW),図面番号",
                "EF-R-2,排風機,200,0.75,E-025",
                "EF-R-2,送風機,200,1.5,E-025",
                "EF-R-2,排風機,200,0.75,E-044",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    row = _read_rows(out_csv)[0]
    assert row["台数判定"] == "✗"
    assert row["容量判定"] == "要確認"
    assert row["総合判定"] == "要確認"
    assert row["判定理由"] == "容量が複数候補"


def test_merge_outputs_only_display_marks_for_judgments(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,送風機,1.5,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nA-1,送風機,200,1.5\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    rows = _read_rows(out_csv)
    for row in rows:
        for col in ["総合判定", "台数判定", "容量判定", "名称判定", "機器ID照合"]:
            assert row[col] in {"◯", "✗", "要確認"}

    raw = out_csv.read_text(encoding="utf-8-sig")
    assert "match" not in raw
    assert "mismatch" not in raw
    assert "review" not in raw
