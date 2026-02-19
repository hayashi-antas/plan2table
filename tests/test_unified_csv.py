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
    assert header[:5] == ["総合判定", "台数判定", "容量判定", "名称判定", "判定理由"]
    assert "照合結果" not in header
    assert "不一致内容" not in header
    assert "確認理由" not in header
    assert "名称差異" not in header

    a1 = rows[0]
    assert a1["総合判定"] == "要確認"
    assert a1["台数判定"] == "✗"
    assert a1["容量判定"] == "要確認"
    assert a1["名称判定"] == "✗"
    assert a1["判定理由"] == "容量が複数候補"
    assert a1["機器ID"] == "A-1"
    assert a1["盤表 記載名"] == "送風機,予備"
    assert a1["盤表 容量(kW)"] == "1.5,2"
    assert a1["台数差"] == "1"
    assert a1["容量差(kW)"] == ""
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
    assert b1["判定理由"] == "盤表に記載なし"
    assert b1["機器ID"] == "B-1"
    assert b1["盤表 台数"] == "0"
    assert b1["盤表 記載トレース"] == ""


def test_merge_marks_non_numeric_capacity_as_review(tmp_path):
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
    assert row["総合判定"] == "要確認"
    assert row["台数判定"] == "◯"
    assert row["容量判定"] == "要確認"
    assert row["名称判定"] == "◯"
    assert row["判定理由"] == "容量が数値でない"


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
    assert raster_only["判定理由"] == "機器表に記載なし"

    missing_id = rows[2]
    assert missing_id["機器ID"] == ""
    assert missing_id["総合判定"] == "要確認"
    assert missing_id["台数判定"] == "要確認"
    assert missing_id["容量判定"] == "要確認"
    assert missing_id["名称判定"] == "要確認"
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
        for col in ["総合判定", "台数判定", "容量判定", "名称判定"]:
            assert row[col] in {"◯", "✗", "要確認"}

    raw = out_csv.read_text(encoding="utf-8-sig")
    assert "match" not in raw
    assert "mismatch" not in raw
    assert "review" not in raw
