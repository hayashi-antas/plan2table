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
                "機器番号,機器名称,電圧（V）,容量(KW)",
                "A-1,送風機,200,1.5",
                "A-1,送風機,200,2.0",
                "A-1,予備,100,1.5",
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
    assert not out_csv.read_bytes().startswith(b"\xef\xbb\xbf")

    rows = _read_rows(out_csv)
    assert len(rows) == 2

    a1 = rows[0]
    assert a1["機械番号"] == "A-1"
    assert a1["raster_容量(kW)_values"] == "1.5 / 2.0"
    assert a1["raster_機器名称"] == "送風機 / 予備"
    assert a1["raster_電圧(V)"] == "200 / 100"
    assert float(a1["raster_容量(kW)_sum"]) == 5.0
    assert a1["raster_match_count"] == "3"
    assert a1["raster_台数_calc"] == "3"
    assert float(a1["vector_容量(kW)_calc"]) == 3.0
    assert float(a1["容量差分(kW)"]) == 2.0
    assert float(a1["台数差分"]) == 1.0
    assert a1["存在判定(○/×)"] == "○"
    assert a1["台数判定(○/×)"] == "×"
    assert a1["容量判定(○/×)"] == "×"
    assert a1["総合判定(○/×)"] == "×"
    assert a1["不一致理由"] == "台数差分=1"

    b1 = rows[1]
    assert b1["機械番号"] == "B-1"
    assert b1["raster_match_count"] == "0"
    assert b1["raster_台数_calc"] == "0"
    assert b1["raster_機器名称"] == ""
    assert b1["存在判定(○/×)"] == "×"
    assert b1["台数判定(○/×)"] == "×"
    assert b1["容量判定(○/×)"] == "×"
    assert b1["総合判定(○/×)"] == "×"
    assert b1["不一致理由"] == "rasterなし"


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
    assert row["存在判定(○/×)"] == "○"
    assert row["台数判定(○/×)"] == "○"
    assert row["容量判定(○/×)"] == "×"
    assert row["総合判定(○/×)"] == "×"
    assert row["不一致理由"] == "容量欠損"
