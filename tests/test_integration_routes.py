import csv
import io
import re
from uuid import uuid4

from fastapi.testclient import TestClient

import main as app_main
from extractors import job_store


client = TestClient(app_main.app)


def _extract_download_path(html: str, kind: str) -> str:
    pattern = rf"/jobs/[0-9a-f\-]+/{kind}\.csv"
    m = re.search(pattern, html)
    assert m, f"download path for {kind} was not found"
    return m.group(0)


def test_raster_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")

    def fake_extract_raster_pdf(**kwargs):
        out_csv = kwargs["out_csv"]
        out_csv.write_text("機器番号,機器名称,電圧(V),容量(kW)\nA-1,送風機,200,1.5\n", encoding="utf-8")
        return {"rows": 1, "columns": ["機器番号", "機器名称", "電圧(V)", "容量(kW)"]}

    monkeypatch.setattr(app_main, "extract_raster_pdf", fake_extract_raster_pdf)

    resp = client.post(
        "/raster/upload",
        files={"file": ("raster.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    path = _extract_download_path(resp.text, "raster")

    dl = client.get(path)
    assert dl.status_code == 200
    assert "A-1" in dl.text


def test_vector_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)

    def fake_extract_vector_pdf_four_columns(pdf_path, out_csv_path):
        out_csv_path.write_text("機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nV-1,排風機,2.2,1\n", encoding="utf-8")
        return {"rows": 1, "columns": ["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数"]}

    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", fake_extract_vector_pdf_four_columns)

    resp = client.post(
        "/vector/upload",
        files={"file": ("vector.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    path = _extract_download_path(resp.text, "vector")

    dl = client.get(path)
    assert dl.status_code == 200
    assert "V-1" in dl.text


def test_fixed_download_returns_404_when_missing():
    missing_job = str(uuid4())
    assert client.get(f"/jobs/{missing_job}/raster.csv").status_code == 404
    assert client.get(f"/jobs/{missing_job}/vector.csv").status_code == 404


def test_fixed_download_rejects_invalid_job_id_format():
    assert client.get("/jobs/not-a-uuid/raster.csv").status_code == 422


def test_upload_route_compat_delegates_to_area_upload(monkeypatch):
    async def fake_area_upload(file):
        return "<div>compat-ok</div>"

    monkeypatch.setattr(app_main, "handle_area_upload", fake_area_upload)

    resp = client.post(
        "/upload",
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert "compat-ok" in resp.text


def test_unified_merge_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)

    raster_job = job_store.create_job(kind="raster", source_filename="raster.pdf")
    vector_job = job_store.create_job(kind="vector", source_filename="vector.pdf")

    (raster_job.job_dir / "raster.csv").write_text(
        "\n".join(
            [
                "機器番号,機器名称,電圧(V),容量(Kw)",
                "A-1,送風機,200,1.5",
                "A-1,送風機,200,2.0",
                "A-1,予備,100,1.5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (vector_job.job_dir / "vector.csv").write_text(
        "機器番号,名称,動力(50Hz)_消費電力(Kw),台数\nA-1,排風機,1.5,2\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/unified/merge",
        data={
            "raster_job_id": raster_job.job_id,
            "vector_job_id": vector_job.job_id,
        },
    )
    assert resp.status_code == 200
    path = _extract_download_path(resp.text, "unified")

    dl = client.get(path)
    assert dl.status_code == 200

    rows = list(csv.DictReader(io.StringIO(dl.text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["機器番号"] == "A-1"
    assert row["raster_容量(kW)_values"] == "1.5 / 2.0"
    assert row["raster_機器名称"] == "送風機 / 予備"
    assert row["raster_電圧(V)"] == "200 / 100"
    assert float(row["raster_容量(kW)_sum"]) == 5.0
    assert row["raster_match_count"] == "3"
    assert row["raster_台数_calc"] == "3"
    assert float(row["台数差分"]) == 1.0
    assert float(row["容量差分(kW)"]) == 2.0
    assert row["存在判定(○/×)"] == "○"
    assert row["台数判定(○/×)"] == "×"
    assert row["容量判定(○/×)"] == "×"
    assert row["総合判定(○/×)"] == "×"
    assert row["不一致理由"] == "台数差分=1"

    m = re.search(r"/jobs/([0-9a-f\-]+)/unified\.csv", path)
    assert m
    unified_job_id = m.group(1)
    unified_csv_path = tmp_path / unified_job_id / "unified.csv"
    raw = unified_csv_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_unified_merge_returns_404_when_job_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)

    missing_id = str(uuid4())
    resp = client.post(
        "/unified/merge",
        data={
            "raster_job_id": missing_id,
            "vector_job_id": missing_id,
        },
    )
    assert resp.status_code == 404


def test_unified_merge_returns_404_when_csv_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    raster_job = job_store.create_job(kind="raster", source_filename="raster.pdf")
    vector_job = job_store.create_job(kind="vector", source_filename="vector.pdf")

    resp = client.post(
        "/unified/merge",
        data={
            "raster_job_id": raster_job.job_id,
            "vector_job_id": vector_job.job_id,
        },
    )
    assert resp.status_code == 404


def test_unified_merge_rejects_invalid_uuid():
    resp = client.post(
        "/unified/merge",
        data={"raster_job_id": "not-a-uuid", "vector_job_id": "not-a-uuid"},
    )
    assert resp.status_code == 422
