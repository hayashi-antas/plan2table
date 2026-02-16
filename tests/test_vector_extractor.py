import pytest

from extractors import vector_extractor as ve


class _FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeTable:
    def __init__(self, bbox):
        self.bbox = bbox


def _row(key="", name="", power="", count=""):
    row = [""] * ve.CELL_COUNT
    row[0] = key
    row[1] = name
    row[9] = power
    row[15] = count
    return row


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CAV-3～6-1", True),
        ("CAV-11～15", True),
        ("OS-1", True),
        ("AH-1", True),
        ("OS- AH-1", True),
        ("機器番号", False),
        ("注記事項", False),
        ("", False),
    ],
)
def test_looks_like_equipment_code_accepts_expected_patterns(value, expected):
    assert ve.looks_like_equipment_code(value) is expected


def test_extract_records_keeps_cav_os_ah_rows_and_stops_only_on_note_markers():
    rows = [
        _row("DF-R-1", "臭突ファン", "0.4", "1"),
        _row("SMF-2", "排煙機", "5.5", "1"),
        _row("CAV-3～6-1", "定風量装置", "10VA", "4"),
        _row("CAV-7-1", "(OA)", "", "1"),
        _row("CAV-11～15", "-1", "", "5"),
        _row("OS- 1", "脱臭装置", "0.15", "1"),
        _row("AH-1", "ｴｱ搬送ﾌｧﾝ", "0.11", "7"),
        _row("注記事項", "", "", ""),
        _row("CAV-2-IGNORE", "should not be extracted", "10VA", "1"),
    ]

    records, note_rows = ve.extract_records(rows)

    ids = [r[0] for r in records]
    assert ids == [
        "DF-R-1",
        "SMF-2",
        "CAV-3～6-1",
        "CAV-7-1",
        "CAV-11～15-1",
        "OS-1",
        "AH-1",
    ]
    assert note_rows == 1
    assert records[2][9] == "10VA"
    assert records[2][15] == "4"
    assert records[4][1] == ""


def test_extract_pdf_to_rows_aggregates_target_tables_from_all_pages(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    pages = ["page-1", "page-2", "page-3"]
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))

    table_map = {
        "page-1": [_FakeTable((0, 0, 1, 1)), _FakeTable((1, 0, 2, 1))],
        "page-2": [_FakeTable((0, 0, 1, 1)), _FakeTable((1, 0, 2, 1))],
        "page-3": [],
    }
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: table_map[page])
    monkeypatch.setattr(ve, "collect_grid_lines", lambda page, bbox: ([0.0], [0.0]))
    monkeypatch.setattr(ve, "extract_grid_rows", lambda page, vertical, horizontal: [["raw"]])

    header_call_count = {"count": 0}

    def fake_reconstruct_headers(page, bbox, vertical):
        header_call_count["count"] += 1
        return ["H1"], ["H2"]

    monkeypatch.setattr(ve, "reconstruct_headers_from_pdf", fake_reconstruct_headers)

    records_queue = iter(
        [
            ([["P1-L"]], 0),
            ([["P1-R"]], 1),
            ([["P2-L"]], 0),
            ([["P2-R"]], 2),
        ]
    )
    monkeypatch.setattr(ve, "extract_records", lambda rows: next(records_queue))

    rows, note_rows, headers = ve.extract_pdf_to_rows(pdf_path)

    assert rows == [["H1"], ["H2"], ["P1-L"], ["P1-R"], ["P2-L"], ["P2-R"]]
    assert note_rows == 3
    assert headers == [["H1"], ["H2"]]
    assert header_call_count["count"] == 1


def test_extract_pdf_to_rows_raises_when_no_target_tables_in_any_page(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    pages = ["page-1", "page-2"]
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: [])

    with pytest.raises(ValueError, match="No target tables found in any PDF page."):
        ve.extract_pdf_to_rows(pdf_path)
