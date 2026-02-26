import asyncio
import concurrent.futures
import csv
import io
import re
import threading
from urllib.parse import unquote
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
    assert kwargs["page"] == 0
    out_csv = kwargs["out_csv"]
    out_csv.write_text(
        "機器番号,機器名称,電圧(V),容量(kW),図面番号\nA-1,送風機,200,1.5,E-024\n",
        encoding="utf-8",
    )
    return {"rows": 1, "columns": ["機器番号", "機器名称", "電圧(V)", "容量(kW)", "図面番号"]}


def _fake_vector_extract_success(pdf_path, out_csv_path):
    out_csv_path.write_text(
        "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数,図面番号\nA-1,排風機,1.5,1,M-001\n",
        encoding="utf-8",
    )
    return {"rows": 1, "columns": ["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数", "図面番号"]}


def _fake_e055_extract_success(**kwargs):
    out_csv = kwargs["out_csv"]
    out_csv.write_text(
        "器具記号,メーカー,相当型番\n直付LED,Panasonic,NNN111\n直付LED,ODELIC,OD222\n",
        encoding="utf-8-sig",
    )
    return {"rows": 2, "columns": ["器具記号", "メーカー", "相当型番"]}


def _fake_e251_extract_success(**kwargs):
    out_csv = kwargs["out_csv"]
    out_csv.write_text(
        "器具記号,メーカー,相当型番\nD1,DAIKO,LZD-93195XW\nD2,DNL,D-EX12\nL1,DAIKO,DSY-4394YWG\n,Panasonic,WTF4088CWK\n",
        encoding="utf-8-sig",
    )
    return {"rows": 4, "columns": ["器具記号", "メーカー", "相当型番"]}


def _fake_e142_extract_success(**kwargs):
    out_csv = kwargs["out_csv"]
    out_csv.write_text(
        "メインコントローラ,MC-N0190,電源電圧,AC100V,消費電流,0.8A以下\n"
        "漏水センサー,MS-D1220\n",
        encoding="utf-8-sig",
    )
    return {"rows": 2, "columns": ["column_1", "column_2", "column_3", "column_4"]}


def test_raster_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")

    def fake_extract_raster_pdf(**kwargs):
        assert kwargs["page"] == 0
        out_csv = kwargs["out_csv"]
        out_csv.write_text(
            "機器番号,機器名称,電圧(V),容量(kW),図面番号\nA-1,送風機,200,1.5,E-024\n",
            encoding="utf-8",
        )
        return {"rows": 1, "columns": ["機器番号", "機器名称", "電圧(V)", "容量(kW)", "図面番号"]}

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


def test_e055_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_e055_pdf", _fake_e055_extract_success)

    resp = client.post(
        "/e-055/upload",
        files={"file": ("e055.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert re.search(r'data-kind="e055"\s+data-job-id="[0-9a-f\-]+"', resp.text)
    path = _extract_download_path(resp.text, "e055")

    dl = client.get(path)
    assert dl.status_code == 200
    csv_text = dl.content.decode("utf-8-sig")
    assert "器具記号,メーカー,相当型番" in csv_text
    assert "Panasonic,NNN111" in csv_text
    assert "ODELIC,OD222" in csv_text


def test_e055_upload_returns_error_when_vision_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "")

    resp = client.post(
        "/e-055/upload",
        files={"file": ("e055.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert "VISION_SERVICE_ACCOUNT_KEY is not configured." in resp.text


def test_e055_upload_contract_unchanged_across_line_assist_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_e055_pdf", _fake_e055_extract_success)

    baseline_csv_text = ""
    for mode in ("off", "auto", "force"):
        monkeypatch.setenv("E055_LINE_ASSIST_MODE", mode)
        resp = client.post(
            "/e-055/upload",
            files={"file": ("e055.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        assert resp.status_code == 200
        assert 'data-status="success"' in resp.text
        path = _extract_download_path(resp.text, "e055")
        dl = client.get(path)
        assert dl.status_code == 200
        csv_text = dl.content.decode("utf-8-sig")
        if mode == "off":
            baseline_csv_text = csv_text
            assert "器具記号,メーカー,相当型番" in csv_text
        else:
            assert csv_text == baseline_csv_text


def test_e251_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_e251_pdf", _fake_e251_extract_success)

    resp = client.post(
        "/e-251/upload",
        files={"file": ("e251.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert re.search(r'data-kind="e251"\s+data-job-id="[0-9a-f\-]+"', resp.text)
    path = _extract_download_path(resp.text, "e251")

    dl = client.get(path)
    assert dl.status_code == 200
    csv_text = dl.content.decode("utf-8-sig")
    assert "器具記号,メーカー,相当型番" in csv_text
    assert "D1,DAIKO,LZD-93195XW" in csv_text
    assert "D2,DNL,D-EX12" in csv_text


def test_e251_upload_returns_error_when_vision_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "")

    resp = client.post(
        "/e-251/upload",
        files={"file": ("e251.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert "VISION_SERVICE_ACCOUNT_KEY is not configured." in resp.text


def test_e142_upload_and_download_fixed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")
    monkeypatch.setattr(app_main, "extract_e142_pdf", _fake_e142_extract_success)

    resp = client.post(
        "/e-142/upload",
        files={"file": ("e142.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert re.search(r'data-kind="e142"\s+data-job-id="[0-9a-f\-]+"', resp.text)
    path = _extract_download_path(resp.text, "e142")

    dl = client.get(path)
    assert dl.status_code == 200
    csv_text = dl.content.decode("utf-8-sig")
    assert "メインコントローラ,MC-N0190,電源電圧,AC100V" in csv_text
    assert "漏水センサー,MS-D1220" in csv_text


def test_e142_upload_returns_error_when_vision_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "")

    resp = client.post(
        "/e-142/upload",
        files={"file": ("e142.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert resp.status_code == 200
    assert 'data-status="error"' in resp.text
    assert "VISION_SERVICE_ACCOUNT_KEY is not configured." in resp.text


def test_fixed_download_returns_404_when_missing():
    missing_job = str(uuid4())
    assert client.get(f"/jobs/{missing_job}/raster.csv").status_code == 404
    assert client.get(f"/jobs/{missing_job}/vector.csv").status_code == 404
    assert client.get(f"/jobs/{missing_job}/e055.csv").status_code == 404
    assert client.get(f"/jobs/{missing_job}/e251.csv").status_code == 404
    assert client.get(f"/jobs/{missing_job}/e142.csv").status_code == 404


def test_fixed_download_rejects_invalid_job_id_format():
    assert client.get("/jobs/not-a-uuid/raster.csv").status_code == 422
    assert client.get("/jobs/not-a-uuid/e055.csv").status_code == 422
    assert client.get("/jobs/not-a-uuid/e251.csv").status_code == 422
    assert client.get("/jobs/not-a-uuid/e142.csv").status_code == 422


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
    assert 'href="/e-055"' in root.text
    assert 'href="/e-251"' in root.text
    assert 'href="/e-142"' in root.text
    assert 'hx-post="/customer/run"' not in root.text

    me_check = client.get("/me-check")
    assert me_check.status_code == 200
    assert 'hx-post="/customer/run"' in me_check.text
    assert 'name="panel_file"' in me_check.text
    assert 'name="equipment_file"' in me_check.text

    develop = client.get("/me-check/develop")
    assert develop.status_code == 200
    assert 'hx-post="/raster/upload"' in develop.text
    assert 'hx-post="/vector/upload"' in develop.text
    assert 'hx-post="/unified/merge"' in develop.text

    e055 = client.get("/e-055")
    assert e055.status_code == 200
    assert 'hx-post="/e-055/upload"' in e055.text

    e251 = client.get("/e-251")
    assert e251.status_code == 200
    assert 'hx-post="/e-251/upload"' in e251.text

    e142 = client.get("/e-142")
    assert e142.status_code == 200
    assert 'hx-post="/e-142/upload"' in e142.text


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
    assert 'data-action="expand-customer-table"' in resp.text

    assert "総合判定" in resp.text
    assert "判定理由" in resp.text
    assert "名称判定" in resp.text
    assert "機器ID照合" in resp.text
    assert "機器ID" in resp.text
    assert "機器表 記載名" in resp.text
    assert "盤表 記載名" in resp.text
    assert "名称差異" not in resp.text
    assert "機器表 台数" in resp.text
    assert "盤表 台数" in resp.text
    assert "台数差" in resp.text
    assert "台数判定" in resp.text
    assert "機器表 消費電力(kW)" in resp.text
    assert "盤表 容量(kW)" in resp.text
    assert "盤表 記載トレース" in resp.text
    assert "容量差(kW)" in resp.text
    assert "容量判定" in resp.text
    assert "機器表 図面番号" in resp.text
    assert "盤表 図面番号" in resp.text
    assert "M-001" in resp.text
    assert "送風機" in resp.text
    assert "E-024" in resp.text
    assert "raster_機器名称" not in resp.text
    assert "vector_容量(kW)_calc" not in resp.text
    assert "台数差 / 容量差は 盤表 - 機器表" in resp.text
    assert "機器表記載：1件" in resp.text
    assert "盤表記載：1件" in resp.text
    assert "完全一致：0件" in resp.text
    assert "不一致：1件" in resp.text
    assert "要確認：0件" in resp.text

    dl = client.get(f"/jobs/{unified_job_id}/unified.csv")
    assert dl.status_code == 200
    assert "総合判定" in dl.text
    assert "判定理由" in dl.text
    assert "機器ID照合" in dl.text
    assert "機器表 図面番号" in dl.text
    assert "盤表 記載名" in dl.text
    assert "名称差異" not in dl.text
    assert "盤表 記載トレース" in dl.text
    assert "盤表 図面番号" in dl.text
    assert "M-001" in dl.text
    assert "E-024" in dl.text


def test_customer_run_summary_uses_vector_raster_row_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(app_main, "vision_service_account_json", "{\"type\":\"service_account\"}")

    def fake_extract_raster_pdf(**kwargs):
        out_csv = kwargs["out_csv"]
        out_csv.write_text(
            "\n".join(
                [
                    "機器番号,機器名称,電圧(V),容量(kW),図面番号",
                    "A-1,送風機,200,1.5,E-024",
                    "A-1,送風機,200,1.5,E-031",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return {"rows": 2, "columns": ["機器番号", "機器名称", "電圧(V)", "容量(kW)", "図面番号"]}

    def fake_extract_vector_pdf_four_columns(pdf_path, out_csv_path):
        out_csv_path.write_text(
            "機器番号,名称,動力 (50Hz)_消費電力 (KW),台数,図面番号\nA-1,排風機,1.5,1,M-001\n",
            encoding="utf-8",
        )
        return {"rows": 1, "columns": ["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数", "図面番号"]}

    monkeypatch.setattr(app_main, "extract_raster_pdf", fake_extract_raster_pdf)
    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", fake_extract_vector_pdf_four_columns)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert "機器表記載：1件" in resp.text
    assert "盤表記載：2件" in resp.text


@pytest.mark.parametrize(
    ("judgment_header", "raw_mark", "expected_mark"),
    [
        ("照合結果", "一致", "◯"),
        ("総合判定", "○", "◯"),
        ("総合判定(◯/✗)", "✗", "✗"),
        ("総合判定(○/×)", "×", "✗"),
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
            "台数差",
            "機器表 消費電力(kW)",
            "盤表 容量(kW)",
            "容量差(kW)",
            "判定理由",
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
                    "台数差": "0",
                    "機器表 消費電力(kW)": "1.5",
                    "盤表 容量(kW)": "1.5",
                    "容量差(kW)": "0",
                    "判定理由": "",
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
    monkeypatch.setattr(app_main, "extract_vector_pdf_four_columns", _fake_vector_extract_success)

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


def test_customer_run_executes_raster_and_vector_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setenv("ME_CHECK_PARALLEL_EXTRACT", "1")

    raster_started = threading.Event()
    vector_started = threading.Event()
    both_started = threading.Event()

    def fake_raster_job(file_bytes, source_filename):
        raster_started.set()
        if not vector_started.wait(timeout=2):
            raise RuntimeError("vector did not start in parallel")
        both_started.set()
        job = job_store.create_job(kind="raster", source_filename=source_filename)
        (job.job_dir / "raster.csv").write_text("機器番号,機器名称\nA-1,送風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "機器名称"]}

    def fake_vector_job(file_bytes, source_filename):
        vector_started.set()
        if not raster_started.wait(timeout=2):
            raise RuntimeError("raster did not start in parallel")
        both_started.set()
        job = job_store.create_job(kind="vector", source_filename=source_filename)
        (job.job_dir / "vector.csv").write_text("機器番号,名称\nA-1,排風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "名称"]}

    def fake_unified_job(raster_job_id, vector_job_id):
        job = job_store.create_job(kind="unified", source_filename=f"{raster_job_id}+{vector_job_id}")
        (job.job_dir / "unified.csv").write_text("照合結果\n一致\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["照合結果"]}

    monkeypatch.setattr(app_main, "_run_raster_job", fake_raster_job)
    monkeypatch.setattr(app_main, "_run_vector_job", fake_vector_job)
    monkeypatch.setattr(app_main, "_run_unified_job", fake_unified_job)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert both_started.is_set()


def test_customer_run_falls_back_to_sequential_when_parallel_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setenv("ME_CHECK_PARALLEL_EXTRACT", "0")

    execution_order = []
    raster_done = threading.Event()

    def fake_raster_job(file_bytes, source_filename):
        execution_order.append("raster")
        raster_done.set()
        job = job_store.create_job(kind="raster", source_filename=source_filename)
        (job.job_dir / "raster.csv").write_text("機器番号,機器名称\nA-1,送風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "機器名称"]}

    def fake_vector_job(file_bytes, source_filename):
        execution_order.append("vector")
        assert raster_done.is_set()
        job = job_store.create_job(kind="vector", source_filename=source_filename)
        (job.job_dir / "vector.csv").write_text("機器番号,名称\nA-1,排風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "名称"]}

    def fake_unified_job(raster_job_id, vector_job_id):
        job = job_store.create_job(kind="unified", source_filename=f"{raster_job_id}+{vector_job_id}")
        (job.job_dir / "unified.csv").write_text("照合結果\n一致\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["照合結果"]}

    monkeypatch.setattr(app_main, "_run_raster_job", fake_raster_job)
    monkeypatch.setattr(app_main, "_run_vector_job", fake_vector_job)
    monkeypatch.setattr(app_main, "_run_unified_job", fake_unified_job)

    resp = client.post(
        "/customer/run",
        files={
            "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
            "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
        },
    )
    assert resp.status_code == 200
    assert 'data-status="success"' in resp.text
    assert execution_order == ["raster", "vector"]


def test_customer_run_prefers_panel_stage_when_both_extracts_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setenv("ME_CHECK_PARALLEL_EXTRACT", "1")

    def fake_raster_job(file_bytes, source_filename):
        raise RuntimeError("panel raster failed")

    def fake_vector_job(file_bytes, source_filename):
        raise RuntimeError("equipment vector failed")

    monkeypatch.setattr(app_main, "_run_raster_job", fake_raster_job)
    monkeypatch.setattr(app_main, "_run_vector_job", fake_vector_job)

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

    captured = capsys.readouterr()
    assert "Customer flow failed at panel->raster: panel raster failed" in captured.out
    assert "Customer flow failed at equipment->vector: equipment vector failed" in captured.out


def test_customer_run_handles_non_exception_base_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setenv("ME_CHECK_PARALLEL_EXTRACT", "1")

    class NonExceptionFailure(BaseException):
        pass

    def fake_raster_job(file_bytes, source_filename):
        raise NonExceptionFailure("non-exception failure")

    def fake_vector_job(file_bytes, source_filename):
        job = job_store.create_job(kind="vector", source_filename=source_filename)
        (job.job_dir / "vector.csv").write_text("機器番号,名称\nA-1,排風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "名称"]}

    monkeypatch.setattr(app_main, "_run_raster_job", fake_raster_job)
    monkeypatch.setattr(app_main, "_run_vector_job", fake_vector_job)

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
    assert "message: non-exception failure" in resp.text


def test_customer_run_reraises_cancelled_error_from_parallel_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    monkeypatch.setenv("ME_CHECK_PARALLEL_EXTRACT", "1")

    def fake_raster_job(file_bytes, source_filename):
        raise asyncio.CancelledError()

    def fake_vector_job(file_bytes, source_filename):
        job = job_store.create_job(kind="vector", source_filename=source_filename)
        (job.job_dir / "vector.csv").write_text("機器番号,名称\nA-1,排風機\n", encoding="utf-8")
        return job, {"rows": 1, "columns": ["機器番号", "名称"]}

    monkeypatch.setattr(app_main, "_run_raster_job", fake_raster_job)
    monkeypatch.setattr(app_main, "_run_vector_job", fake_vector_job)

    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        client.post(
            "/customer/run",
            files={
                "panel_file": ("panel.pdf", b"%PDF-1.4\n", "application/pdf"),
                "equipment_file": ("equipment.pdf", b"%PDF-1.4\n", "application/pdf"),
            },
        )


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
                "R-9,還気ファン,200,0.75",
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
    content_disposition = dl.headers.get("content-disposition", "")
    decoded_disposition = unquote(content_disposition)
    assert re.search(
        r"me-check_照合結果_\d{8}_\d{4}\.csv",
        decoded_disposition,
    )

    rows = list(csv.DictReader(io.StringIO(dl.content.decode("utf-8-sig"))))
    assert len(rows) == 2
    row = rows[0]
    assert row["総合判定"] == "要確認"
    assert row["判定理由"] == "容量が複数候補"
    assert row["名称判定"] == "✗"
    assert row["機器ID照合"] == "◯"
    assert row["機器ID"] == "A-1"
    assert row["機器表 記載名"] == "排風機"
    assert row["盤表 記載名"] == "送風機,予備"
    assert "名称差異" not in row
    assert float(row["機器表 台数"]) == 2.0
    assert row["盤表 台数"] == "3"
    assert float(row["台数差"]) == 1.0
    assert row["台数判定"] == "✗"
    assert float(row["機器表 消費電力(kW)"]) == 1.5
    assert row["盤表 容量(kW)"] == "1.5,2"
    assert (
        row["盤表 記載トレース"]
        == "図面:? 名称:送風機 容量:1.5 || "
        "図面:? 名称:送風機 容量:2.0 || "
        "図面:? 名称:予備 容量:1.5"
    )
    assert row["容量差(kW)"] == ""
    assert row["容量判定"] == "要確認"
    assert row["機器表 図面番号"] == ""
    assert row["盤表 図面番号"] == ""

    raster_only = rows[1]
    assert raster_only["機器ID"] == "R-9"
    assert raster_only["総合判定"] == "✗"
    assert raster_only["判定理由"] == "機器表に記載なし"
    assert raster_only["機器表 記載名"] == ""
    assert raster_only["盤表 記載名"] == "還気ファン"
    assert "名称差異" not in raster_only
    assert raster_only["機器表 台数"] == ""
    assert raster_only["盤表 台数"] == "1"
    assert raster_only["機器表 消費電力(kW)"] == ""
    assert float(raster_only["盤表 容量(kW)"]) == 0.75
    assert raster_only["台数判定"] == "✗"
    assert raster_only["容量判定"] == "✗"
    assert raster_only["名称判定"] == "✗"
    assert raster_only["機器ID照合"] == "✗"
    assert raster_only["盤表 記載トレース"] == ""
    assert raster_only["容量差(kW)"] == ""
    assert raster_only["機器表 図面番号"] == ""
    assert raster_only["盤表 図面番号"] == ""

    m = re.search(r"/jobs/([0-9a-f\-]+)/unified\.csv", path)
    assert m
    unified_job_id = m.group(1)
    unified_csv_path = tmp_path / unified_job_id / "unified.csv"
    raw = unified_csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")


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
