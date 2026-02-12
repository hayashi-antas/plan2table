from uuid import UUID

from extractors import job_store


def test_job_store_create_save_and_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)

    job = job_store.create_job(kind="raster", source_filename="sample.pdf")
    assert UUID(job.job_id).version == 4
    assert job.job_dir.exists()

    csv_path = job_store.save_csv(job, b"a,b\n1,2\n")
    assert csv_path.name == "raster.csv"
    assert csv_path.exists()

    meta_path = job_store.save_metadata(job, {"row_count": 1, "columns": ["a", "b"]})
    assert meta_path.exists()

    resolved = job_store.resolve_job_csv_path(job.job_id, "raster")
    assert resolved == csv_path


def test_job_store_rejects_non_v4_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_ROOT", tmp_path)
    try:
        job_store.resolve_job_csv_path("00000000-0000-0000-0000-000000000000", "raster")
        raise AssertionError("Expected ValueError")
    except ValueError:
        pass
