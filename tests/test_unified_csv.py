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
                "機械番号,名称,動力(50Hz)_消費電力(Kw),台数",
                "A-1,排風機,1.5,2",
                "B-1,送風機,2.0,1",
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
                "A-1,送風機,200,2.0,E-024",
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
    assert a1["機器名"] == "排風機"
    assert float(a1["機器表 台数"]) == 2.0
    assert a1["盤表 台数"] == "3"
    assert float(a1["台数差（盤表-機器表）"]) == 1.0
    assert float(a1["機器表 消費電力(kW)"]) == 1.5
    assert float(a1["盤表 容量(kW)"]) == 1.5
    assert float(a1["容量差(kW)"]) == 0.0
    assert a1["図面番号"] == "E-024"
    assert a1["照合結果"] == "不一致"
    assert a1["不一致内容"] == "台数差分=1"

    a1_extra = rows[1]
    assert a1_extra["機器ID"] == "A-1"
    assert a1_extra["照合結果"] == ""
    assert a1_extra["不一致内容"] == ""
    assert a1_extra["機器表 台数"] == ""
    assert a1_extra["盤表 台数"] == ""
    assert a1_extra["図面番号"] == "E-024"
    assert float(a1_extra["機器表 消費電力(kW)"]) == 1.5
    assert float(a1_extra["盤表 容量(kW)"]) == 2.0
    assert float(a1_extra["容量差(kW)"]) == 0.5

    b1 = rows[2]
    assert b1["機器ID"] == "B-1"
    assert b1["盤表 台数"] == "0"
    assert b1["盤表 容量(kW)"] == ""
    assert b1["図面番号"] == ""
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
    assert row["図面番号"] == ""
