# M-E-Check 詳細リファレンス

このドキュメントは M-E-Check の技術仕様・内部処理・API の詳細をまとめたリファレンスです。  
概要や使い方は [m-e-check.md](m-e-check.md) を参照してください。

> **注**: 現時点では、**決まった形式のPDF**（機器表は1ページ目に横並び2表、盤表は所定レイアウトの1ページ目など）のみを想定しており、それ以外の形式では正しく動作しない場合があります。

---

## 目次

- [1. 用語定義](#term-definitions)
- [2. 入力・出力仕様](#io-spec)
- [3. ユーザー向けUIの詳細](#ui)
  - [3.1 ページとルート](#ui-route)
  - [3.2 画面の流れ](#ui-flow)
  - [3.3 簡易表に表示される列](#ui-columns)
- [4. 内部処理の全体像](#internal-overview)
  - [4.1 処理の3段階](#internal-stages)
  - [4.2 ジョブストア](#job-store)
- [5. PDF から CSV への抽出](#pdf-to-csv)
  - [5.1 盤表PDF → Raster CSV（raster_extractor）](#raster-extractor)
    - [5.1.1 処理の流れ](#raster-flow)
    - [5.1.2 依存関係](#raster-deps)
    - [5.1.3 Vision API の結果の整理（表にするまで）](#raster-vision)
  - [5.2 機器表PDF → Vector CSV（vector_extractor）](#vector-extractor)
    - [5.2.1 処理の流れ](#vector-flow)
    - [5.2.2 列名のゆれへの対応](#vector-aliases)
    - [5.2.3 依存関係](#vector-deps)
- [6. 統合処理（Unified CSV）](#unified)
  - [6.1 列の対応（エイリアス）](#unified-aliases)
  - [6.2 Raster 側の集約](#unified-raster)
  - [6.3 結合と判定](#unified-merge)
  - [6.4 出力列（unified CSV）](#unified-output)
- [7. API・エンドポイント一覧（M-E-Check 関連）](#api)
- [8. 開発者向け: Develop ページ](#develop)
- [9. 環境・設定](#env)

---

<a id="term-definitions"></a>
## 1. 用語定義

以下では、**機器表PDFを Vector 抽出・盤表PDFを Raster 抽出** する前提で用語を説明しています。これは、現状の実装で利用しているサンプルが「機器表＝ベクトルPDF・盤表＝ラスターPDF」であるためであり、仮の対応です。別形式のPDFを扱う場合は別の抽出パスが必要になります。

| 用語 | 説明 |
|------|------|
| **機器表** | 換気機器表など、機器番号・名称・消費電力・台数などを記載した表。**現状の想定**ではPDFはベクトル（テキスト/表線）で構成される。 |
| **盤表** | 電力制御盤の機器一覧。機器番号・機器名称・電圧・容量(kW)などを記載。**現状の想定**ではPDFはラスター（スキャン画像）として扱う。 |
| **Vector** | 機器表PDFから **pdfplumber**[^1] で表構造を検出し抽出したCSV。内部では「vector」ジョブとして扱う。 |
| **Raster** | 盤表PDFを **pdftoppm**[^3] と **Pillow**[^4] で画像化し、**Google Cloud Vision API**[^2] でOCRして、4列表として抽出したCSV。内部では「raster」ジョブとして扱う。 |
| **Unified** | Vector CSV と Raster CSV を **機器番号で結合** し、存在・台数・容量の一致判定を付与した統合CSV。 |

---

<a id="io-spec"></a>
## 2. 入力・出力仕様

- **入力**
  - **機器表PDF**（[equipment_file](../main.py#L871)）: 換気機器表など、表形式のPDF。1ページ目に2つの横並び表がある想定。
  - **盤表PDF**（[panel_file](../main.py#L871)）: 電力制御盤表。1ページ目を画像化してOCRする想定。
- **出力**
  - **HTML**: 照合結果の主要列（照合結果・不一致内容・機器ID・機器名・機器表 台数・盤表 台数・台数差・容量差など）を簡易表示。
  - **CSV**（`unified.csv`）: 統合結果の10列（[OUTPUT_COLUMNS](../extractors/unified_csv.py#L23)）。ダウンロード用エンドポイント `GET /jobs/{job_id}/unified.csv` で取得。

---

<a id="ui"></a>
## 3. ユーザー向けUIの詳細

<a id="ui-route"></a>
### 3.1 ページとルート

- **`GET /me-check`**  
  M-E-Check 用の単一ページを表示する。テンプレートは `templates/me-check.html`。（ルート定義は [main.py](../main.py) を参照）

<a id="ui-flow"></a>
### 3.2 画面の流れ

1. 画面上で **機器表PDF** と **盤表PDF** の2つのファイルを選択（ドラッグ＆ドロップまたはファイル選択）。
2. 「実行する」ボタンで送信すると、フォームは **`POST /customer/run`** に `multipart/form-data` で送信される（HTMX の `hx-post` 使用）。処理は [handle_customer_run](../main.py#L871) が受け持つ。
3. 処理中は `#customer-loading` が表示され、「PDFを処理中です…」と表示される。
4. 成功時: `#customer-result` に統合結果の **簡易HTML表** が差し替えられ、CSVダウンロードリンクが表示される。
5. 失敗時: `#customer-result` に **エラー用HTML**（stage と message）が返る。

<a id="ui-columns"></a>
### 3.3 簡易表に表示される列

[main.py](../main.py) の [CUSTOMER_TABLE_COLUMNS](../main.py#L487) で定義されている。表示ラベルと、unified CSV の列名の対応は以下のとおりである。**照合結果** は HTML 表示時に「一致」「不一致」に正規化される（○/× なども同じ意味として扱う）。

| 表示ラベル | 対応する unified CSV の列（候補のいずれか） |
|------------|---------------------------------------------|
| 照合結果 | 照合結果（値は「一致」「不一致」に正規化） |
| 不一致内容 | 不一致内容、不一致理由 |
| 機器ID | 機器ID、機器番号、機械番号 |
| 機器名 | 機器名、名称、機器名称 |
| 機器表 台数 | 機器表 台数、台数、vector_台数_numeric |
| 盤表 台数 | 盤表 台数、raster_match_count、raster_台数_calc |
| 台数差（盤表-機器表） | 台数差（盤表-機器表）、台数差分 |
| 機器表 容量合計(kW) | 機器表 容量合計(kW)、vector_容量(kW)_calc |
| 盤表 容量合計(kW) | 盤表 容量合計(kW)、raster_容量(kW)_sum |
| 容量差(kW) | 容量差(kW)、容量差分(kW) |

---

<a id="internal-overview"></a>
## 4. 内部処理の全体像

<a id="internal-stages"></a>
### 4.1 処理の3段階

[handle_customer_run](../main.py#L871)（`POST /customer/run`）では、次の3段階が **同期的に** 実行される。

1. **Panel → Raster**  
   [panel_file](../main.py#L871) のバイト列を [_run_raster_job](../main.py#L625) に渡す。盤表PDFを画像化し、Vision API でOCRして **raster.csv** を生成し、raster ジョブとして保存する。
2. **Equipment → Vector**  
   [equipment_file](../main.py#L871) のバイト列を [_run_vector_job](../main.py#L658) に渡す。機器表PDFから pdfplumber で表を抽出し、**vector.csv**（4列）を生成し、vector ジョブとして保存する。
3. **Unified**  
   [_run_unified_job](../main.py#L694)(raster_job_id, vector_job_id) で、既存の raster.csv と vector.csv を読み、[merge_vector_raster_csv](../extractors/unified_csv.py#L104) により **unified.csv** を生成する。結果は unified ジョブとして保存され、その `job_id` で CSV ダウンロードと簡易表の表示に使われる。

いずれかの段階で例外が発生した場合は、その時点で **エラー用HTML** が返り、以降の段階は実行されない。エラー時の `stage` は `panel->raster` / `equipment->vector` / `unified` のいずれかである。

<a id="job-store"></a>
### 4.2 ジョブストア

- ジョブは [extractors.job_store](../extractors/job_store.py) で管理される。
- ルートディレクトリは [JOBS_ROOT](../extractors/job_store.py#L10) = `Path("/tmp/plan2table/jobs")`。
- 各ジョブは UUID v4 の `job_id` に対応するディレクトリ [JOBS_ROOT](../extractors/job_store.py#L10) / job_id を持ち、その中に以下が保存される。
  - **raster**: `input.pdf`, `raster.csv`, `debug/`（デバッグ画像）, `metadata.json`
  - **vector**: `input.pdf`, `vector.csv`, `metadata.json`
  - **unified**: `unified.csv`, `metadata.json`（`source_job_ids` で raster/vector の job_id を参照）

CSV の実体ファイル名は kind に応じて `raster.csv` / `vector.csv` / `unified.csv` で固定である。

---

<a id="pdf-to-csv"></a>
## 5. PDF から CSV への抽出

<a id="raster-extractor"></a>
### 5.1 盤表PDF → Raster CSV（raster_extractor）

**モジュール**: [extractors.raster_extractor](../extractors/raster_extractor.py)

盤表PDFは「画像として扱う」前提である。スキャンされたPDFや、テキストが画像として埋め込まれているPDFを想定している。

<a id="raster-flow"></a>
#### 5.1.1 処理の流れ

1. **PDF → 画像**  
   `pdftoppm` で指定ページ（デフォルト1ページ目）を PNG に変換する（DPI はデフォルト 300）。[run_pdftoppm](../extractors/raster_extractor.py#L130) がこれを実行する。
2. **左右分割**  
   画像を縦の中央で **左(L) と 右(R)** に分割する（[SIDE_SPLITS](../extractors/raster_extractor.py#L31)）。盤表が2段や2列に分かれているレイアウトに対応する。
3. **OCR**  
   各サイド画像を **Google Cloud Vision API** の `document_text_detection` に送り、単語単位のテキストとバウンディングボックスを取得する（[extract_words](../extractors/raster_extractor.py#L171)）。
4. **行クラスタリング**  
   Y座標に基づき単語を **行** にグループ化する（[cluster_by_y](../extractors/raster_extractor.py#L211)）。しきい値は [y_cluster](../extractors/raster_extractor.py#L504)（デフォルト 20.0 px）。
5. **列境界の推定**  
   ヘッダー行らしき行から「機器番号」「機器名称」「電圧(V)」「容量(kW)」の4列の境界（[ColumnBounds](../extractors/raster_extractor.py#L99)）を推定する（[infer_column_bounds](../extractors/raster_extractor.py#L253)）。ヘッダーキーワード（機器・記号・名称・電圧・容量など）のスコアでヘッダー行を選び、その単語のX座標から列の区切りを決める。
6. **データ行の抽出**  
   ヘッダーより下の領域（[DATA_START_OFFSET](../extractors/raster_extractor.py#L38) 以降）の単語を、列境界に従って4列に割り当て、行ごとにまとめる（[rows_from_words](../extractors/raster_extractor.py#L503)）。  
   ヘッダー行・フッター行はキーワードで除外し、**データ行** のみを残す（[is_data_row](../extractors/raster_extractor.py#L475)）。機器番号のパターン（例: `[A-Z]{1,4}-[A-Z0-9]{1,6}`）や、名称に含まれるキーワード（ポンプ・排風・送風など）でデータ行を判定する。
7. **セル正規化**  
   機器番号と名称の混入補正、単位表記の統一（例: 1/200 → 1φ200）、名称の表記ゆれ（湧水ポンプ→清水ポンプ）などを [normalize_row_cells](../extractors/raster_extractor.py#L404) で行う。
8. **CSV出力**  
   列は [OUTPUT_COLUMNS](../extractors/raster_extractor.py#L29) = `["機器番号", "機器名称", "電圧(V)", "容量(kW)"]` の4列。左右両サイドの行をまとめて `raster.csv` に書き出す。デバッグ用に `debug_dir` へ列境界や単語ボックスを描画した画像を保存する。

<a id="raster-deps"></a>
#### 5.1.2 依存関係

- **poppler-utils** の `pdftoppm`（PDF→PNG）
- **Google Cloud Vision API**（`google-cloud-vision`）。認証は `VISION_SERVICE_ACCOUNT_KEY` 環境変数にサービスアカウントJSONを渡して行う。
- **Pillow**（画像の分割・デバッグ描画）

Raster 抽出が有効に動くには、アプリ起動時に `VISION_SERVICE_ACCOUNT_KEY` が設定されている必要がある。未設定の場合、[_run_raster_job](../main.py#L625) 内で `ValueError` が発生する。

<a id="raster-vision"></a>
#### 5.1.3 Vision API の結果の整理（表にするまで）

Vision API は「この画像にどんな文字が、どこにあったか」を **単語ごと** に返すだけである。**行・列の区切りは返してくれない**。そのため、「どの単語が何行目の何列目か」を **アプリ側で決めて**、4列の表（raster.csv）に組み立てている。

**Vision API が返すもの と 最終的に欲しいもの**

| Vision API が返すもの | 最終的に欲しいもの（raster.csv） |
|----------------------|----------------------------------|
| 単語のテキスト ＋ その単語の矩形座標（bounding_box）の羅列 | 行ごとに「機器番号」「機器名称」「電圧(V)」「容量(kW)」の4列が並んだ表 |

**整理の流れ（何をしているか）**

| 段階 | やっていること | コード上の主なもの |
|------|----------------|---------------------|
| 1. 単語をまとめる | 返ってきた単語に、中心座標（cx, cy）と矩形（bbox）を付けて **WordBox** として保持 | [extract_words](../extractors/raster_extractor.py#L171) → [WordBox](../extractors/raster_extractor.py#L85)（dataclass） |
| 2. 行に分ける | Y座標が近い単語を「同じ行」として **RowCluster** にまとめる | [cluster_by_y](../extractors/raster_extractor.py#L211) → [RowCluster](../extractors/raster_extractor.py#L93)（dataclass） |
| 3. 列を決める | ヘッダー行の単語のX座標から、4列の境界 **ColumnBounds** を推定 | [infer_column_bounds](../extractors/raster_extractor.py#L253) → [ColumnBounds](../extractors/raster_extractor.py#L99)（dataclass） |
| 4. セルに割り当て | 各行の単語を、X座標で列境界と照らして「機器番号列」「名称列」… に振り分け | [assign_column](../extractors/raster_extractor.py#L382), [rows_from_words](../extractors/raster_extractor.py#L503) |
| 5. 表として出力 | データ行だけ残し、表記ゆれを補正してから **csv** で書き出し | [normalize_row_cells](../extractors/raster_extractor.py#L404), `write_csv`（標準ライブラリ） |

**使っている型（すべて raster_extractor 内の dataclass）**

| 型名 | 役割 |
|------|------|
| **[WordBox](../extractors/raster_extractor.py#L85)** | 単語1つ分。テキスト・中心(cx,cy)・矩形(bbox) を持つ。Vision の返り値を入れる入れ物。 |
| **[RowCluster](../extractors/raster_extractor.py#L93)** | 「同じ行」とみなした単語の集まり。行のY座標(row_y) と、その行に属する WordBox のリスト(words)。 |
| **[ColumnBounds](../extractors/raster_extractor.py#L99)** | 4列の境界のX座標。ヘッダーから推定した「ここより左が1列目、ここから2列目…」の区切り。 |

**使っているライブラリ（表の「構造」には表用ライブラリは使っていない）**

| 種類 | ライブラリ | 役割 |
|------|------------|------|
| Vision | `google.cloud.vision` | 画像を送って、単語テキスト＋座標の一覧をもらう。 |
| 画像 | Pillow（`PIL.Image`, `ImageDraw`） | 画像の読み込み・左右分割・デバッグ用の枠描画。 |
| 表の組み立て | **なし**（自前ロジック） | 行・列の割り当てやヘッダー／データ行の判定は、すべて raster_extractor 内のコード。pandas や表解析ライブラリは使っていない。 |
| その他 | 標準ライブラリのみ | `csv`（CSV書き出し）、`re`、`unicodedata`、`dataclasses`、`statistics.median` など。 |

つまり、**Vision の結果を「表」に並べ替える部分は、すべてこのモジュール内のロジック** で、外部の表用ライブラリには頼っていない。

<details>
<summary><strong>なぜこのアプリで Pandas を使わないか（クリックで開く）</strong></summary>

この処理で難しいのは **「どの単語が何行・何列か」を座標から決める部分**（Y で行クラスタ、X で列境界の推定と割り当て、機器番号・名称の表記ゆれ補正）である。これは **座標やドメイン固有ルール** の処理であり、pandas が得意とする「既に表になっているデータの集計・結合・ピボット」とは種類が違う。pandas に任せられる部分はほとんどない。

pandas を使うと「行のリストができたあと」のフィルタや CSV 出力を DataFrame で書くことはできるが、**難しいロジックはそのまま**なので処理は簡単にならない。そのうえ **依存が増える**（pandas はサイズが大きい）。現状は stdlib の `csv` と dataclass だけで完結しており、**依存を増やさず、処理の流れも追いやすい**ため、このモジュールではあえて Pandas を使っていない。
</details>

---

<a id="vector-extractor"></a>
### 5.2 機器表PDF → Vector CSV（vector_extractor）

**モジュール**: [extractors.vector_extractor](../extractors/vector_extractor.py)

機器表PDFは **ベクトル（テキスト・線）** で構成されている前提である。表の罫線やセルが PDF の描画命令として存在し、pdfplumber で表として検出できる形式を想定している。

<a id="vector-flow"></a>
#### 5.2.1 処理の流れ

1. **表の検出**  
   `pdfplumber` で1ページ目を開き、`page.find_tables()` で表を検出する。  
   **条件**: 幅がページ幅の 40% 以上、かつページ下端 85% より上にある表のみを対象とする。この条件を満たす表が **ちょうど2つ** あることを期待し、左からソートして「左側の表」「右側の表」として扱う（[pick_target_tables](../extractors/vector_extractor.py#L67)）。2つでない場合は `ValueError` を上げる。
2. **グリッド線の取得**  
   各表の bbox 内で、PDF の線オブジェクトから縦線・横線を収集し、クラスタリングして **セル境界** として使う（[collect_grid_lines](../extractors/vector_extractor.py#L87)）。縦線は 19+1 本（[CELL_COUNT](../extractors/vector_extractor.py#L22) = 19 列）、横線は 4 本以上あることを要求する。
3. **表のセル抽出**  
   縦・横の境界を `explicit` で指定して `page.extract_table()` を呼び、行×列の二次元リストを得る（[extract_grid_rows](../extractors/vector_extractor.py#L145)）。1行あたり最大 19 セルまでを正規化して扱う。
4. **ヘッダーの復元**  
   表領域内の単語から、1行目データより上をヘッダーとみなし、グループ行・サブ行・単位行を再構成する（[reconstruct_headers_from_pdf](../extractors/vector_extractor.py#L187)）。「換気機器表」などのタイトルや、最初の機器番号の位置からデータ開始位置を決め、その上をヘッダーとして扱う。
5. **データ行の抽出**  
   先頭列が機器番号パターン（[looks_like_equipment_code](../extractors/vector_extractor.py#L35)、例: `SF-P-1`, `EF-B2-3`）の行をレコードの開始とし、続く行は同じレコードにマージする（[extract_records](../extractors/vector_extractor.py#L321)）。「記記事」「注記事項」等が出てきたらデータ終端とする。
6. **4列CSVの生成**  
   フル表ではなく、**統合用の4列** だけを切り出す（[build_four_column_rows](../extractors/vector_extractor.py#L506)）。  
   列対応は次のとおり:
   - 機器番号: 元表の 0 列目
   - 名称: 1 列目
   - 動力 (50Hz)_消費電力 (KW): 9 列目
   - 台数: 15 列目  

   ヘッダーは `["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数"]` である。この4列だけが **vector.csv** として書き出され、unified 統合の「vector 側」入力になる。

<a id="vector-aliases"></a>
#### 5.2.2 列名のゆれへの対応

unified 側では、機器番号・消費電力・台数などの列名が PDF や表によって微妙に異なる場合がある。そのため [unified_csv](../extractors/unified_csv.py) モジュールでは [COLUMN_ALIASES](../extractors/unified_csv.py#L8) で複数の表記を許容している（後述）。

<a id="vector-deps"></a>
#### 5.2.3 依存関係

- **pdfplumber**: PDF のページ・表・線・テキストの取得。外部OCRや Vision API は使わない。

---

<a id="unified"></a>
## 6. 統合処理（Unified CSV）

**モジュール**: [extractors.unified_csv](../extractors/unified_csv.py)

[merge_vector_raster_csv](../extractors/unified_csv.py#L104)(vector_csv_path, raster_csv_path, out_csv_path) が、vector CSV と raster CSV を **機器番号** をキーに結合し、判定列を付与して unified CSV を生成する。

<a id="unified-aliases"></a>
### 6.1 列の対応（エイリアス）

両方のCSVで「実質同じ意味の列」を複数のヘッダー名で受け付ける。

| 正規キー | 許容されるヘッダー名の例（入力 vector / raster CSV 用） |
|----------|--------------------------------------------------------|
| equipment_id | 機器番号, 機械番号 |
| vector_name | 名称, 機器名称 |
| vector_power_per_unit_kw | 動力 (50Hz)_消費電力 (KW), 動力(50Hz)_消費電力(KW), 動力(50Hz)_消費電力(Kw) 等 |
| vector_count | 台数 |
| raster_name | 機器名称, 名称 |
| raster_voltage | 電圧(V), 電圧（V） |
| raster_capacity_kw | 容量(kW), 容量(KW), 容量(Kw), 容量（kW） 等 |

ヘッダーは NFKC 正規化し、空白・全角空白を除いた小文字で比較してマッチさせる（[_normalize_header](../extractors/unified_csv.py#L39)）。機器番号の結合キーは **大文字化・空白除去** した値（[_normalize_key](../extractors/unified_csv.py#L45)）を使う。

<a id="unified-raster"></a>
### 6.2 Raster 側の集約

Raster CSV では、**同じ機器番号** が複数行にまたがることがある（1台あたり1行の記載など）。unified では raster を **機器番号でグループ化** し、次のように扱う。

- マッチした行数: `raster_match_count`（＝盤表にその機器番号が何行あるか）
- 容量(kW): **数値として解釈できる値のみ合計**（`raster_容量(kW)_sum`）。数値化できない値は合計に含めない。
- 機器名称・電圧・容量の生値も内部で収集するが、**現行の unified.csv（10列）には出力しない**。

<a id="unified-merge"></a>
### 6.3 結合と判定

- **主軸は Vector** である。vector の各行（機器番号）に対して、同じ機器番号の raster 集約結果を1つ紐づける。
- **機器名**: unified の `機器名` は vector 側の名称列（`vector_name`）を採用する。
- **存在判定**: その機器番号が raster に1行以上あるか。なければ「盤表に記載なし」。
- **台数**: vector の「台数」と raster のマッチ行数を比較。一致で ○、不一致で ×。差分は **台数差（盤表-機器表）** として出力する。
- **容量**: vector 側は `消費電力(kW)/台 × 台数` で **機器表 容量合計(kW)** を計算。raster 側は集約した容量合計を **盤表 容量合計(kW)** として出力。その差が **容量差(kW)**。  
  **容量判定**: 容量差の絶対値が [EPS_KW](../extractors/unified_csv.py#L36)（0.1 kW）以下なら ○、それ以外は ×。どちらかが欠損している場合は「容量欠損」として **不一致内容** に入れる。
- **照合結果**: 存在 ○ かつ 台数 ○ かつ 容量 ○ のときのみ「一致」、それ以外は「不一致」。
- **不一致内容**: 照合が「不一致」のとき、次の優先順で **1つだけ** 設定する。  
  1) `盤表に記載なし`  
  2) `台数差分=...`（台数が欠損時は `台数差分=欠損`）  
  3) `容量欠損`  
  4) `容量差分=...`

<a id="unified-output"></a>
### 6.4 出力列（unified CSV）

unified CSV は **vector の生データ列は含めず**、次の [OUTPUT_COLUMNS](../extractors/unified_csv.py#L23) の10列だけを出力する。

| 列名 |
|------|
| 照合結果 |
| 不一致内容 |
| 機器ID |
| 機器名 |
| 機器表 台数 |
| 盤表 台数 |
| 台数差（盤表-機器表） |
| 機器表 容量合計(kW) |
| 盤表 容量合計(kW) |
| 容量差(kW) |

---

<a id="api"></a>
## 7. API・エンドポイント一覧（M-E-Check 関連）

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/me-check` | M-E-Check 用UI（me-check.html）を返す。 |
| POST | `/customer/run` | 機器表PDF・盤表PDFを multipart で受け取り、raster → vector → unified の順で実行し、結果をHTML（簡易表 or エラー）で返す。 |
| GET | `/jobs/{job_id}/unified.csv` | 指定した unified ジョブの `unified.csv` をダウンロードする（ダウンロード名は `me-check_照合結果_YYYYMMDD_HHMM.csv`）。 |

`/customer/run` のパラメータは `panel_file`（盤表PDF）と `equipment_file`（機器表PDF）の2つが必須。  
拡張子が `.pdf` でない場合はエラーHTMLが返る（現行実装はファイル内容のMIME判定までは行わない）。

---

<a id="develop"></a>
## 8. 開発者向け: Develop ページ

`GET /develop` で開く **M-E-Check Develop** ページ（`templates/develop.html`）では、M-E-Check の処理を **段階的に** 試せる。

- **Raster**: 盤表PDFを1本だけアップロードし、`POST /raster/upload` で raster.csv を生成。Job ID と CSV ダウンロードリンクが返る。
- **Vector**: 機器表PDFを1本だけアップロードし、`POST /vector/upload` で vector.csv を生成。同様に Job ID とダウンロードリンク。
- **Unified**: Raster と Vector の **既存の Job ID** をフォームで指定し、`POST /unified/merge` で統合のみ実行。unified.csv のダウンロードリンクが得られる。

お客さん向けの `/me-check` は「2ファイルを一度に送って全部やる」フローであり、Develop は「Raster / Vector / Unified を個別に実行して挙動を確認する」ためのコンソールである。

---

<a id="env"></a>
## 9. 環境・設定

- **Raster 抽出**
  - `VISION_SERVICE_ACCOUNT_KEY`: Google Cloud Vision API 用のサービスアカウントJSON（文字列）。未設定だと raster ジョブで `ValueError`。
  - システムに `pdftoppm`（poppler）がインストールされている必要あり。
- **Vector 抽出**
  - 追加の環境変数は不要。pdfplumber が PDF を開ける環境であればよい。
- **ジョブ保存先**
  - `/tmp/plan2table/jobs`。本番では永続化やクリーンアップ戦略の検討が必要。

---

[^1]: `pdfplumber` は、PDF 内のテキストや表・罫線の構造を解析して、Python からテキストやテーブルデータを抽出するためのライブラリです。
[^2]: `Google Cloud Vision API` は、画像やPDF内の文字や物体を機械学習モデルで解析し、OCR（テキスト抽出）や物体検出などの画像認識機能をアプリから呼び出せる Google Cloud のAPIサービスです。
[^3]: `pdftoppm` は、Poppler に含まれるコマンドラインツールで、PDF のページを PNG などの画像に変換します。本アプリでは `subprocess` で実行し、盤表PDFの1ページ目を画像化しています。
[^4]: `Pillow` は、Python で画像の読み込み・切り抜き・保存などを行う画像処理ライブラリです。本アプリでは pdftoppm が出力した PNG を開き、左右に分割するために使っています。
