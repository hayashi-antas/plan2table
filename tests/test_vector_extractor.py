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


class _FakeCrop:
    def __init__(self, text=""):
        self._text = text

    def extract_text(self):
        return self._text

    def extract_words(self, **kwargs):
        return []


class _FakePage:
    def __init__(
        self,
        name="page",
        crop_text="",
        width=1000.0,
        height=1000.0,
        tables=None,
    ):
        self.name = name
        self._crop_text = crop_text
        self.width = width
        self.height = height
        self._tables = tables or []

    def crop(self, bbox):
        return _FakeCrop(self._crop_text)

    def find_tables(self):
        return self._tables


class _FakeTableRow:
    def __init__(self, cells):
        self.cells = cells
        non_empty = [c for c in cells if c is not None]
        if non_empty:
            self.bbox = (
                min(c[0] for c in non_empty),
                min(c[1] for c in non_empty),
                max(c[2] for c in non_empty),
                max(c[3] for c in non_empty),
            )
        else:
            self.bbox = (0.0, 0.0, 0.0, 0.0)


class _FakeCellTable:
    def __init__(self, bbox, rows):
        self.bbox = bbox
        self._rows = rows
        self.rows = []
        for row_index, row in enumerate(rows):
            cells = []
            for col_index, _ in enumerate(row):
                x0 = float(col_index * 10)
                y0 = float(row_index * 5)
                x1 = float(x0 + 10)
                y1 = float(y0 + 5)
                cells.append((x0, y0, x1, y1))
            self.rows.append(_FakeTableRow(cells))

    def extract(self):
        return self._rows


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


def test_extract_records_does_not_merge_continuation_text_into_equipment_id():
    rows = [
        _row("PAC-1-1", "空 調 機", "0.04", "2"),
        _row("PAC-1-", "", "", ""),
        _row("", "", "", ""),
    ]

    records, note_rows = ve.extract_records(rows)

    assert note_rows == 0
    assert len(records) == 1
    assert records[0][0] == "PAC-1-1"


def test_extract_records_merges_repeated_same_equipment_id_block():
    rows = [
        _row("PAC-3", "空 調 機", "(冷)0.575", "1"),
        _row("PAC-3", "", "(冷)0.575", ""),
        _row("", "店舗用", "(暖)0.721", ""),
    ]

    records, note_rows = ve.extract_records(rows)

    assert note_rows == 0
    assert len(records) == 1
    assert records[0][0] == "PAC-3"
    assert records[0][1] == "空 調 機 / 店舗用"
    assert records[0][9] == "(冷)0.575 / (暖)0.721"
    assert records[0][15] == "1"


def test_extract_records_stops_on_black_square_note_marker():
    rows = [
        _row("PAC-15-1", "空 調 機", "", ""),
        _row("", "", "", ""),
        _row("", "■集中リモコン", "", "1組"),
        _row("PAC-16-1", "空 調 機", "0.11", "1"),
    ]

    records, note_rows = ve.extract_records(rows)

    assert note_rows == 1
    assert len(records) == 1
    assert records[0][0] == "PAC-15-1"
    assert records[0][15] == ""


def test_extract_records_summary_continuation_keeps_single_count_and_joins_name_without_slash():
    rows = [
        _row("RC-8", "ルームエアコン", "", "22"),
        _row("", "室外機2段積架台", "", "5"),
    ]

    records, note_rows = ve.extract_records(rows)

    assert note_rows == 0
    assert len(records) == 1
    assert records[0][0] == "RC-8"
    assert records[0][1] == "ルームエアコン 室外機2段積架台"
    assert records[0][15] == "22"


def test_normalize_summary_name_fixes_known_room_aircon_artifact():
    assert ve._normalize_summary_name("ルームエアコ マルチタイプン") == "ルームエアコン マルチタイプ"


def test_extract_rows_via_table_cells_supports_kigou_and_quantity_headers():
    rows = [
        ["空調機器表", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        [
            "記 号",
            "名 称",
            "系 統",
            "仕 様",
            "",
            "動 力 FAN･圧縮機 相 消費電力 始動 操作 監視 種別 出力(KW) P-V(KW) 方式",
            "",
            "",
            "",
            "",
            "",
            "",
            "数量",
            "設置場所",
            "",
            "備考",
            "",
        ],
        ["", "", "", "", "", "", "相 P-V", "消費電力 (KW)", "始動", "操作", "監視", "種別", "", "階", "室名", "", ""],
        ["PAC-1", "空 調 機", "B2F", "室内機", "", "(FAN)0.16", "1-200", "0.19", "", "", "", "", "1", "2F", "機械室", "型番", ""],
    ]
    table = _FakeCellTable((0, 0, 10, 10), rows)
    page = _FakePage("fallback-page")

    projected = ve._extract_rows_via_table_cells(page, table)

    assert projected[3][0] == "PAC-1"
    assert projected[3][1] == "空 調 機"
    assert projected[3][9] == "0.19"
    assert projected[3][15] == "1"


def test_extract_rows_from_summary_left_table_supports_id_name_total_mapping():
    rows = [
        ["空調・換気機器表", "", "", "", "", "", ""],
        ["機器番号", "", "名 称", "", "仕様", "", "合 計 86 戸"],
        ["", "", "", "", "", "", ""],
        ["HEX－", "1", "全熱交換", "機", "", "", "9"],
        ["ORC-", "4", "ルームエアコ", "ン", "", "", "12"],
        ["RC-1～3", "", "欠 番", "", "", "", "16"],
    ]
    table = _FakeCellTable((40, 30, 650, 760), rows)
    page = _FakePage("summary-page", width=1200.0, height=840.0)

    projected = ve._extract_rows_from_summary_left_table(page, table)
    records, _ = ve.extract_records(projected)

    ids = [r[0] for r in records]
    assert "HEX-1" in ids
    assert "ORC-4" in ids
    assert "RC-1～3" in ids
    record_map = {r[0]: r for r in records}
    assert record_map["HEX-1"][1] == "全熱交換機"
    assert record_map["HEX-1"][15] == "9"
    assert record_map["ORC-4"][1] == "ルームエアコン"
    assert record_map["ORC-4"][15] == "12"


def test_extract_rows_from_summary_left_table_normalizes_garbled_name_fragment():
    rows = [
        ["空調・換気機器表", "", "", "", "", "", ""],
        ["機器番号", "", "名 称", "", "仕様", "", "合 計 86 戸"],
        ["", "", "", "", "", "", ""],
        ["ORC-2", "", "ルームエアコ マルチタイプ", "ン", "", "", "6"],
    ]
    table = _FakeCellTable((40, 30, 650, 760), rows)
    page = _FakePage("summary-page", width=1200.0, height=840.0)

    projected = ve._extract_rows_from_summary_left_table(page, table)
    records, _ = ve.extract_records(projected)

    assert records[0][0] == "ORC-2"
    assert records[0][1] == "ルームエアコン マルチタイプ"


def test_pick_summary_left_tables_selects_left_main_table():
    main_rows = [
        ["空調・換気機器表", "", "", "", ""],
        ["機器番号", "", "名 称", "", "合計"],
    ]
    note_rows = [
        ["記 事", "主管部署", "", ""],
        ["", "", "", ""],
    ]
    main = _FakeCellTable((40, 30, 650, 760), main_rows)
    note = _FakeCellTable((500, 780, 1130, 820), note_rows)
    page = _FakePage(
        "summary-page",
        width=1200.0,
        height=840.0,
        tables=[main, note],
    )

    picked = ve._pick_summary_left_tables(page)
    assert picked == [main]


def test_select_power_value_candidate_prefers_precise_value_from_candidates():
    current = "(低温)9.4"
    candidates = ["(低温)9.43", "( )7.18", "(冷)9.45"]
    assert ve._select_power_value_candidate(current, candidates) == "(低温)9.43"


def test_extract_pdf_to_rows_uses_cell_fallback_when_grid_detection_fails(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    fallback_rows = [
        ["空調機器表", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["記 号", "名 称", "系 統", "仕 様", "", "動 力", "", "", "", "", "", "", "数量", "設置場所", "", "備考", ""],
        ["", "", "", "", "", "", "相", "消費電力 (KW)", "", "", "", "", "", "階", "室名", "", ""],
        ["PAC-1", "空 調 機", "B2F", "室外機", "", "", "3-200", "0.575", "", "", "", "", "1", "2F", "バルコニー", "型番", ""],
    ]

    page = _FakePage("page-1")
    pages = [page]
    table = _FakeCellTable((0, 0, 10, 10), fallback_rows)
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: [table, table])
    monkeypatch.setattr(
        ve,
        "collect_grid_lines",
        lambda page, bbox: (_ for _ in ()).throw(ValueError("grid fail")),
    )

    rows, note_rows, headers = ve.extract_pdf_to_rows(pdf_path)

    assert note_rows == 0
    assert headers[0][0] == "機器番号"
    assert headers[0][15] == "台数"
    assert [r[0] for r in rows[2:]] == ["PAC-1", "PAC-1"]
    assert all(r[9] == "0.575" for r in rows[2:])
    assert all(r[15] == "1" for r in rows[2:])


def test_extract_pdf_to_rows_fallback_stops_on_black_square_note_marker(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    fallback_rows = [
        ["空調機器表", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["記 号", "名 称", "系 統", "仕 様", "", "動 力", "", "", "", "", "", "", "数量", "設置場所", "", "備考", ""],
        ["", "", "", "", "", "", "相", "消費電力 (KW)", "", "", "", "", "", "階", "室名", "", ""],
        ["PAC-15-1", "空 調 機", "11F", "室内機", "", "", "1-200", "", "", "", "", "", "", "11F", "パーティールーム", "", ""],
        ["", "", "", "■集中リモコン", "", "", "", "", "", "", "", "", "1組", "1F", "中央管理室", "", ""],
    ]

    page = _FakePage("page-1")
    pages = [page]
    table = _FakeCellTable((0, 0, 10, 10), fallback_rows)
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: [table])
    monkeypatch.setattr(
        ve,
        "collect_grid_lines",
        lambda page, bbox: (_ for _ in ()).throw(ValueError("grid fail")),
    )

    rows, note_rows, _ = ve.extract_pdf_to_rows(pdf_path)

    assert note_rows == 1
    assert len(rows) == 3
    assert rows[2][0] == "PAC-15-1"
    assert rows[2][15] == ""


def test_extract_pdf_to_rows_aggregates_target_tables_from_all_pages(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    pages = [_FakePage("page-1"), _FakePage("page-2"), _FakePage("page-3")]
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))

    table_map = {
        "page-1": [_FakeTable((0, 0, 1, 1)), _FakeTable((1, 0, 2, 1))],
        "page-2": [_FakeTable((0, 0, 1, 1)), _FakeTable((1, 0, 2, 1))],
        "page-3": [],
    }
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: table_map[page.name])
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


def test_extract_pdf_to_rows_uses_summary_left_route_when_target_tables_are_missing(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    page = _FakePage("page-1")
    pages = [page]
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: [])

    summary_rows = [
        ["空調・換気機器表", "", "", "", "", "", ""],
        ["機器番号", "", "名 称", "", "仕様", "", "合計"],
        ["", "", "", "", "", "", ""],
        ["ORC-", "4", "ルームエアコ", "ン", "", "", "12"],
    ]
    summary_table = _FakeCellTable((40, 30, 650, 760), summary_rows)
    monkeypatch.setattr(ve, "_pick_summary_left_tables", lambda page: [summary_table])

    rows, note_rows, headers = ve.extract_pdf_to_rows(pdf_path)

    assert note_rows == 0
    assert headers[0][0] == "機器番号"
    assert headers[0][15] == "台数"
    assert len(rows) == 3
    assert rows[2][0] == "ORC-4"
    assert rows[2][1] == "ルームエアコン"
    assert rows[2][9] == ""
    assert rows[2][15] == "12"


def test_extract_pdf_to_rows_raises_when_no_target_tables_in_any_page(tmp_path, monkeypatch):
    pdf_path = tmp_path / "equipment.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    pages = [_FakePage("page-1"), _FakePage("page-2")]
    monkeypatch.setattr(ve.pdfplumber, "open", lambda _: _FakePDF(pages))
    monkeypatch.setattr(ve, "pick_target_tables", lambda page: [])

    with pytest.raises(ValueError, match="No target tables found in any PDF page."):
        ve.extract_pdf_to_rows(pdf_path)
