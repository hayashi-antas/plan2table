import csv
import io
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main as app_main
from extractors import job_store


client = TestClient(app_main.app)


def _extract_download_path(html: str, kind: str) -> str:
    pattern = rf"/jobs/[0-9a-f\-]+/{kind}\.csv"
    m = re.search(pattern, html)
    assert m, f"download path for {kind} was not found"
    return m.group(0)


def _fake_raster_extract_success(**kwargs):
    out_csv = kwargs["out_csv"]
    out_csv.write_text("機器番号,機器名称,電圧(V),容量(kW)\nA-1,送風機,200,1.5\n", encoding="utf-8")
    return {"rows": 1, "columns": ["機器番号", "機器名称", "電圧(V)", "容量(kW)"]}


def _fake_vector_extract_success(pdf_path, out_csv_path):
    out_csv_path.write_text("機器番号,名称,動力 (50Hz)_消費電力 (KW),台数\nA-1,排風機,1.5,1\n", encoding="utf-8")
    return {"rows": 1, "columns": ["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数"]}


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
    assert re.search(r'data-kind="raster"\s+data-job-id="[0-9a-f\-]+"', resp.text)
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
    assert re.search(r'data-kind="vector"\s+data-job-id="[0-9a-f\-]+"', resp.text)
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


def test_root_and_develop_routes_are_split():
    root = client.get("/")
    assert root.status_code == 200
    assert "Plan2Table Portal" in root.text
    assert 'href="/area"' in root.text
    assert 'href="/me-check"' in root.text
    assert 'hx-post="/customer/run"' not in root.text

    me_check = client.get("/me-check")
    assert me_check.status_code == 200
    assert 'hx-post="/customer/run"' in me_check.text
    assert 'name="panel_file"' in me_check.text
    assert 'name="equipment_file"' in me_check.text

    develop = client.get("/develop")
    assert develop.status_code == 200
    assert 'hx-post="/raster/upload"' in develop.text
    assert 'hx-post="/vector/upload"' in develop.text
    assert 'hx-post="/unified/merge"' in develop.text


def test_customer_run_success_returns_contract_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_raster_pdf", _fake_raster_extract_success)
    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", _fake_vector_extract_success)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text

    job_id_match = re.search(r'data-unified-job-id="([0-9a-f\\-]+)"', resp.text)
    assert job_id_match
    unified_job_id = job_id_match.group(1)

    assert f'data-download-url="/jobs/{unified_job_id}/unified.csv"' in resp.text
    assert f'/jobs/{unified_job_id}/unified.csv' in resp.text

    assert "照合結果" in resp.text
    assert "不一致内容" in resp.text
    assert "機器ID" in resp.text
    assert "機器名" in resp.text
    assert "機器表 台数" in resp.text
    assert "盤表 台数" in resp.text
    assert "台数差（盤表-機器表）" in resp.text
    assert "機器表 容量合計(kW)" in resp.text
    assert "盤表 容量合計(kW)" in resp.text
    assert "容量差(kW)" in resp.text
    assert "raster_機器名称" not in resp.text
    assert "vector_容量(kW)_calc" not in resp.text

    dl = client.get(f"/jobs/{unified_job_id}/unified.csv")
    assert dl.status_code == 200
    assert "照合結果" in dl.text


@pytest.mark.parametrize(
    ("judgment_header", "raw_mark", "expected_mark"),
    [
        ("照合結果", "一致", "一致"),
        ("総合判定", "○", "一致"),
        ("総合判定(◯/✗)", "✗", "不一致"),
        ("総合判定(○/×)", "×", "不一致"),
    ],
)
def test_customer_run_handles_judgment_header_variants(
    tmp_path, monkeypatch, judgment_header, raw_mark, expected_mark
):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_raster_pdf", _fake_raster_extract_success)
    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", _fake_vector_extract_success)

    def fake_merge_vector_raster_csv(vector_csv_path, raster_csv_path, out_csv_path):
        fieldnames = [
            "機器番号",
            "名称",
            "機器表 台数",
            "盤表 台数",
            "台数差（盤表-機器表）",
            "機器表 容量合計(kW)",
            "盤表 容量合計(kW)",
            "容量差(kW)",
            "不一致内容",
            judgment_header,
        ]
        with out_csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "機器番号": "A-1",
                    "名称": "排風機",
                    "機器表 台数": "1",
                    "盤表 台数": "1",
                    "台数差（盤表-機器表）": "0",
                    "機器表 容量合計(kW)": "1.5",
                    "盤表 容量合計(kW)": "1.5",
                    "容量差(kW)": "0",
                    "不一致内容": "",
                    judgment_header: raw_mark,
                }
            )
        return {"rows": 1, "columns": fieldnames}

    monkeypatch.setattr(app_main, "merge_vector_raster_csv", fake_merge_vector_raster_csv)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text

    mark_cell = re.search(rf"<td[^>]*>\s*{re.escape(expected_mark)}\s*</td>", resp.text)
    assert mark_cell


def test_customer_run_returns_stage_for_panel_to_raster_error(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")

    def fake_raster_failure(**kwargs):
        raise RuntimeError("panel raster failed")

    monkeypatch.setattr(app_main, "extract_raster_pdf", fake_raster_failure)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert 'data-stage="panel-&gt;raster"' in resp.text
    assert "message: panel raster failed" in resp.text


def test_customer_run_returns_stage_for_equipment_to_vector_error(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_raster_pdf", _fake_raster_extract_success)

    def fake_vector_failure(pdf_path, out_csv_path):
        raise RuntimeError("equipment vector failed")

    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", fake_vector_failure)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert 'data-stage="equipment-&gt;vector"' in resp.text
    assert "message: equipment vector failed" in resp.text


def test_customer_run_returns_stage_for_unified_error(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_raster_pdf", _fake_raster_extract_success)
    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", _fake_vector_extract_success)

    def fake_unified_failure(vector_csv_path, raster_csv_path, out_csv_path):
        raise RuntimeError("unified failed\ndetail")

    monkeypatch.setattr(app_main, "merge_vector_raster_csv", fake_unified_failure)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert 'data-stage="unified"' in resp.text
    assert "message: unified failed detail" in resp.text


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
    assert row["機器ID"] == "A-1"
    assert row["機器名"] == "排風機"
    assert float(row["機器表 台数"]) == 2.0
    assert row["盤表 台数"] == "3"
    assert float(row["台数差（盤表-機器表）"]) == 1.0
    assert float(row["機器表 容量合計(kW)"]) == 3.0
    assert float(row["盤表 容量合計(kW)"]) == 5.0
    assert float(row["容量差(kW)"]) == 2.0
    assert row["照合結果"] == "不一致"
    assert row["不一致内容"] == "台数差分=1"

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
