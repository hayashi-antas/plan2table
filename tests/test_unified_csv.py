import csv
from pathlib import Path

from extractors.unified_csv import merge_vector_raster_csv


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_merge_vector_raster_with_aliases_and_order(tmp_path):
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
    assert result["rows"] == 3
    assert out_csv.exists()
    assert not out_csv.read_bytes().startswith(b"\xef\xbb\xbf")

    rows = _read_rows(out_csv)
    assert len(rows) == 3

    a1 = rows[0]
    assert a1["機器ID"] == "A-1"
    assert a1["機器表 記載名"] == "排風機"
    assert float(a1["機器表 台数"]) == 2.0
    assert a1["盤表 台数"] == "3"
    assert float(a1["台数差"]) == 1.0
    assert a1["盤表 記載名"] == "送風機,予備"
    assert a1["名称差異"] == "あり"
    assert float(a1["機器表 消費電力(kW)"]) == 1.5
    assert float(a1["盤表 容量(kW)"]) == 1.5
    assert float(a1["容量差(kW)"]) == 0.0
    assert a1["機器表 図面番号"] == "M-101"
    assert a1["盤表 図面番号"] == "E-024,E-031"
    assert a1["照合結果"] == "不一致"
    assert a1["不一致内容"] == "台数差分=1"

    a1_extra = rows[1]
    assert a1_extra["機器ID"] == "A-1"
    assert a1_extra["照合結果"] == ""
    assert a1_extra["不一致内容"] == ""
    assert a1_extra["機器表 台数"] == ""
    assert a1_extra["盤表 台数"] == ""
    assert a1_extra["盤表 記載名"] == "送風機,予備"
    assert a1_extra["名称差異"] == "あり"
    assert a1_extra["機器表 図面番号"] == "M-101"
    assert a1_extra["盤表 図面番号"] == "E-024,E-031"
    assert float(a1_extra["機器表 消費電力(kW)"]) == 1.5
    assert float(a1_extra["盤表 容量(kW)"]) == 2.0
    assert float(a1_extra["容量差(kW)"]) == 0.5

    b1 = rows[2]
    assert b1["機器ID"] == "B-1"
    assert b1["盤表 台数"] == "0"
    assert b1["盤表 記載名"] == ""
    assert b1["名称差異"] == ""
    assert b1["盤表 容量(kW)"] == ""
    assert b1["機器表 図面番号"] == "M-102"
    assert b1["盤表 図面番号"] == ""
    assert b1["照合結果"] == "不一致"
    assert b1["不一致内容"] == "盤表に記載なし"


def test_merge_sets_kw_missing_reason_when_capacity_diff_is_empty(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,排風機,1.5,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW)\nA-1,送風機,200,\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    rows = _read_rows(out_csv)
    assert len(rows) == 1
    row = rows[0]
    assert row["照合結果"] == "不一致"
    assert row["不一致内容"] == "容量欠損"
    assert row["機器表 図面番号"] == ""
    assert row["盤表 図面番号"] == ""
    assert row["盤表 記載名"] == "送風機"
    assert row["名称差異"] == "あり"


def test_merge_name_warning_uses_normalized_comparison(tmp_path):
    vector_csv = tmp_path / "vector.csv"
    raster_csv = tmp_path / "raster.csv"
    out_csv = tmp_path / "unified.csv"

    vector_csv.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,送 風 機,1.5,1\n",
        encoding="utf-8",
    )
    raster_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW),図面番号\nA-1,送風機,200,1.5,E-024\n",
        encoding="utf-8",
    )

    merge_vector_raster_csv(
        vector_csv_path=vector_csv,
        raster_csv_path=raster_csv,
        out_csv_path=out_csv,
    )

    rows = _read_rows(out_csv)
    assert len(rows) == 1
    row = rows[0]
    assert row["機器表 記載名"] == "送風機"
    assert row["盤表 記載名"] == "送風機"
    assert row["名称差異"] == ""
    assert row["機器表 図面番号"] == ""


def test_merge_appends_raster_only_rows(tmp_path):
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
    assert len(rows) == 2

    raster_only = rows[1]
    assert raster_only["機器ID"] == "R-9"
    assert raster_only["照合結果"] == "不一致"
    assert raster_only["不一致内容"] == "機器表に記載なし"
    assert raster_only["機器表 記載名"] == ""
    assert raster_only["盤表 記載名"] == "還気ファン"
    assert raster_only["名称差異"] == ""
    assert raster_only["機器表 台数"] == ""
    assert raster_only["盤表 台数"] == "1"
    assert raster_only["機器表 消費電力(kW)"] == ""
    assert raster_only["盤表 容量(kW)"] == "0.75"
    assert raster_only["容量差(kW)"] == ""
    assert raster_only["機器表 図面番号"] == ""
    assert raster_only["盤表 図面番号"] == "E-099"


def test_merge_appends_raster_missing_id_rows_for_review(tmp_path):
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
                ",全自動砂濾過装置,200,2.0,E-024",
                ",全自動砂濾過装置,200,2.0,E-024",
                ",同上用フロートスイッチ,,,E-024",
                ",同上用フロートスイッチ,,,E-024",
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

    missing_id_main = rows[1]
    assert missing_id_main["照合結果"] == "要確認"
    assert missing_id_main["不一致内容"] == "機器ID未記載（盤表）"
    assert missing_id_main["機器ID"] == ""
    assert missing_id_main["盤表 記載名"] == "全自動砂濾過装置"
    assert missing_id_main["盤表 台数"] == "2"
    assert missing_id_main["盤表 容量(kW)"] == "2.0"
    assert missing_id_main["盤表 図面番号"] == "E-024"

    missing_id_sub = rows[2]
    assert missing_id_sub["照合結果"] == "要確認"
    assert missing_id_sub["不一致内容"] == "機器ID未記載（盤表）"
    assert missing_id_sub["機器ID"] == ""
    assert missing_id_sub["盤表 記載名"] == "同上用フロートスイッチ"
    assert missing_id_sub["盤表 台数"] == "2"
    assert missing_id_sub["盤表 容量(kW)"] == ""
    assert missing_id_sub["盤表 図面番号"] == "E-024"
