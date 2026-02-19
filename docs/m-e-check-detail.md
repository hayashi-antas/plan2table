# M-E-Check 詳細リファレンス

このドキュメントは M-E-Check の技術仕様・内部処理・API の詳細をまとめたリファレンスです。  
概要や使い方は [m-e-check.md](m-e-check.md) を参照してください。

> **注**: 現時点では、**決まった形式のPDF**（機器表は上部に横並び2表、または要約左表があるページ構成。盤表は所定の見出し語群を含む表レイアウト）を前提としており、それ以外の形式では正しく動作しない場合があります。

---

## 目次

- [1. 用語定義](#term-definitions)
- [2. 入力・出力仕様](#io-spec)
- [3. ユーザー向けUIの詳細](#ui)
  - [3.1 ページとルート](#ui-route)
  - [3.2 画面の流れ](#ui-flow)
  - [3.3 簡易表に表示される列](#ui-columns)
  - [3.4 サマリ表示](#ui-summary)
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
  - [6.5 実データ検証メモ（2026-02-19）](#unified-validation-20260219)
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
| **Raster** | 盤表PDFを **pdftoppm**[^3] と **Pillow**[^4] で画像化し、**Google Cloud Vision API**[^2] でOCRして、4列表＋図面番号として抽出したCSV。内部では「raster」ジョブとして扱う。 |
| **Unified** | Vector CSV と Raster CSV を **機器番号で結合** し、存在・台数・容量の一致判定を付与した統合CSV。 |

---

<a id="io-spec"></a>
## 2. 入力・出力仕様

- **入力**
  - **機器表PDF**（[equipment_file](../main.py#L1019)）: 換気機器表など、表形式のPDF。実装は全ページを走査し、対象ページごとに「上部2表」または「要約左表（フォールバック）」を抽出する。
  - **盤表PDF**（[panel_file](../main.py#L1018)）: 電力制御盤表。実装は `extract_raster_pdf(page=0)` で全ページを順に画像化してOCRする。
- **出力**
  - **HTML**: 総合判定の主要列（総合判定・判定理由・機器ID・機器表 記載名・台数差・容量差など）を簡易表示。
  - **CSV**（`unified.csv`）: 統合結果の21列（[OUTPUT_COLUMNS](../extractors/unified_csv.py)）。ダウンロード用エンドポイント `GET /jobs/{job_id}/unified.csv` で取得。文字コードは **UTF-8 with BOM (`utf-8-sig`)**。

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
2. 「実行する」ボタンで送信すると、フォームは **`POST /customer/run`** に `multipart/form-data` で送信される（HTMX の `hx-post` 使用）。処理は [handle_customer_run](../main.py#L1017) が受け持つ。
3. 処理中は `#customer-loading` が表示され、「PDFを処理中です…」と表示される。
4. 成功時: `#customer-result` に統合結果の **サマリ + 簡易HTML表** が差し替えられ、CSVダウンロードリンクと「拡大表示」アイコンが表示される。拡大表示では表をモーダルでほぼ全画面表示できる（閉じるボタン / `Esc` キー対応）。
5. 失敗時: `#customer-result` に **エラー用HTML**（stage と message）が返る。

<a id="ui-columns"></a>
### 3.3 簡易表に表示される列

[main.py](../main.py) の `CUSTOMER_TABLE_COLUMNS` で定義されている。表示ラベルと、unified CSV の列名の対応は以下のとおりである。判定値は HTML 表示時に `◯ / ✗ / 要確認` へ正規化される（後方互換で `一致/不一致/判定不可/○/×` も受理）。

| 表示ラベル | 対応する unified CSV の列（候補のいずれか） |
|------------|---------------------------------------------|
| 総合判定 | 総合判定、照合結果、総合判定(◯/✗)、総合判定(○/×) |
| 判定理由 | 判定理由、不一致内容、確認理由、不一致理由 |
| 台数判定 | 台数判定 |
| 容量判定 | 容量判定 |
| 名称判定 | 名称判定 |
| 機器ID | 機器ID、機器番号、機械番号 |
| 機器表 記載名 | 機器表 記載名、機器表記載名、機器名、名称、機器名称 |
| 盤表 記載名 | 盤表 記載名、盤表記載名 |
| 機器表 台数 | 機器表 台数、台数、vector_台数_numeric |
| 盤表 台数 | 盤表 台数、raster_match_count、raster_台数_calc |
| 台数差 | 台数差、台数差（盤表-機器表）、台数差分 |
| 機器表 消費電力(kW) | 機器表 消費電力(kW)、機器表 容量合計(kW)、vector_容量(kW)_calc |
| 盤表 容量(kW) | 盤表 容量(kW)、盤表 容量合計(kW)、raster_容量(kW)_sum |
| 容量差(kW) | 容量差(kW)、容量差分(kW) |
| 機器表 図面番号 | 機器表 図面番号、機器表図面番号 |
| 盤表 図面番号 | 盤表 図面番号、図面番号、図番 |
| 盤表 記載トレース | 盤表 記載トレース |

※ 表の下に `台数差 / 容量差は 盤表 - 機器表` の注記を表示する。

<a id="ui-summary"></a>
### 3.4 サマリ表示

簡易表の上に次の5項目を表示する（形式: `ラベル：N件`）。

- `機器表記載`: vector 抽出（`vector.csv`）の行数
- `盤表記載`: raster 抽出（`raster.csv`）の行数
- `完全一致`: `総合判定 = ◯` の行数
- `不一致`: `総合判定 = ✗` の行数
- `要確認`: `総合判定 = 要確認` の行数

※ `機器表記載` / `盤表記載` は、統合後（unified）の集約行数ではなく、抽出段階の生行数を使う。

---

<a id="internal-overview"></a>
## 4. 内部処理の全体像

<a id="internal-stages"></a>
### 4.1 処理の3段階

[handle_customer_run](../main.py#L1017)（`POST /customer/run`）では、次の3段階が実行される。

1. **Panel → Raster**  
   [panel_file](../main.py#L1018) のバイト列を [_run_raster_job](../main.py#L771) に渡す。`page=0`（全ページ対象）で盤表PDFを画像化し、Vision API でOCRして **raster.csv** を生成し、raster ジョブとして保存する。
2. **Equipment → Vector**  
   [equipment_file](../main.py#L1019) のバイト列を [_run_vector_job](../main.py#L804) に渡す。機器表PDFから pdfplumber で表を抽出し、**vector.csv**（5列）を生成し、vector ジョブとして保存する。
3. **Unified**  
   [_run_unified_job](../main.py#L840)(raster_job_id, vector_job_id) で、既存の raster.csv と vector.csv を読み、[merge_vector_raster_csv](../extractors/unified_csv.py#L601) により **unified.csv** を生成する。結果は unified ジョブとして保存され、その `job_id` で CSV ダウンロードと簡易表の表示に使われる。

※ **Raster と Vector の抽出**は、環境変数 `ME_CHECK_PARALLEL_EXTRACT=1`（既定）のとき **並列実行**される。`0` のときは Raster 完了後に Vector を実行する。

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
   `pdftoppm` で対象ページ（`page=0` のときは全ページ）を PNG に変換する（DPI はデフォルト 300）。
2. **1パス目 OCR（ページ全体画像）**  
   ページ全体を Vision `document_text_detection` に送り、単語（WordBox）を取得する。
3. **ヘッダー行検出（複数表対応）**  
   単語を Yクラスタで行にまとめ、ヘッダー語群（機器番号/名称/電圧/容量）を満たす行を複数検出する。4カテゴリ中3カテゴリ以上で表候補として採用する。
4. **表候補 bbox の生成と重複マージ**  
   ヘッダー位置から表領域を推定し、近接候補や重複候補は IoU/近傍判定で統合する。これにより、四隅や上辺の小表を複数拾える。
5. **2パス目 OCR（候補ごと）**  
   各候補領域を `crop + margin` で切り出して再OCRし、表ごとに列境界を再推定する（小表・1行表の精度を上げる）。
6. **データ行抽出（可変開始Y）**  
   固定オフセットではなく、ヘッダー高さベースで開始Yを算出して4列へ割り当てる。  
   ヘッダー/フッター行を除外し、**機器番号パターン** または **名称+数値（電圧/容量）** を満たす行を採用する。
7. **Hybrid fallback**  
   1〜2ページ目は旧「左右分割」ロジックを優先し、3ページ目以降は新ロジックを優先する。優先ロジックで0行だったページのみ、もう一方へフォールバックする。
8. **CSV出力・図面番号付与**  
   列は `["機器番号", "機器名称", "電圧(V)", "容量(kW)", "図面番号"]` の5列を維持。図面番号はページごとに1つ解決して全行へ付与する。重複機器番号行は保持する。
9. **デバッグ出力**  
   `debug_dir` に `p{n}_headers.png`, `p{n}_tables.png`, `p{n}_table{k}.png` を保存する。旧ロジック（左右分割）が走った場合は `bbox_p{n}_L.png`, `bbox_p{n}_R.png` も保存される。

<a id="raster-deps"></a>
#### 5.1.2 依存関係

- **poppler-utils** の `pdftoppm`（PDF→PNG）
- **Google Cloud Vision API**（`google-cloud-vision`）。認証は `VISION_SERVICE_ACCOUNT_KEY` 環境変数にサービスアカウントJSONを渡して行う。
- **Pillow**（画像の分割・デバッグ描画）
- **pdfplumber**（ページ数カウント、図面番号のテキストレイヤー補助抽出）

Raster 抽出が有効に動くには、アプリ起動時に `VISION_SERVICE_ACCOUNT_KEY` が設定されている必要がある。未設定の場合、[_run_raster_job](../main.py#L771) 内で `ValueError` が発生する。

<a id="raster-vision"></a>
#### 5.1.3 Vision API の結果の整理（表にするまで）

Vision API は「この画像にどんな文字が、どこにあったか」を **単語ごと** に返すだけである。**行・列の区切りは返してくれない**。そのため、「どの単語が何行目の何列目か」を **アプリ側で決めて**、4列の表を組み立て、さらに図面番号を付与して raster.csv にしている。

**Vision API が返すもの と 最終的に欲しいもの**

| Vision API が返すもの | 最終的に欲しいもの（raster.csv） |
|----------------------|----------------------------------|
| 単語のテキスト ＋ その単語の矩形座標（bounding_box）の羅列 | 行ごとに「機器番号」「機器名称」「電圧(V)」「容量(kW)」「図面番号」の5列が並んだ表 |

**整理の流れ（何をしているか）**

| 段階 | やっていること | コード上の主なもの |
|------|----------------|---------------------|
| 1. 単語をまとめる | 返ってきた単語に、中心座標（cx, cy）と矩形（bbox）を付けて **WordBox** として保持 | [extract_words](../extractors/raster_extractor.py#L393) → [WordBox](../extractors/raster_extractor.py#L124)（dataclass） |
| 2. 行に分ける | Y座標が近い単語を「同じ行」として **RowCluster** にまとめる | `cluster_by_y` → [RowCluster](../extractors/raster_extractor.py#L132) |
| 3. 表候補を決める | ヘッダー語群を満たす行を **HeaderAnchor** として検出し、候補 bbox を作る | `detect_header_anchors` / `detect_table_candidates_from_page_words` |
| 4. 候補ごと再OCR | 候補領域を再OCRして列境界 **ColumnBounds** を推定 | `ocr_table_crop` / `infer_column_bounds` |
| 5. セルに割り当て | 各行の単語を、X座標で列境界と照らして4列へ振り分け | `assign_column`, `rows_from_words` |
| 6. 表として出力 | データ行だけ残し、表記ゆれを補正してから **csv** で書き出し | `normalize_row_cells`, `write_csv` |

**使っている型（すべて raster_extractor 内の dataclass）**

| 型名 | 役割 |
|------|------|
| **[WordBox](../extractors/raster_extractor.py#L124)** | 単語1つ分。テキスト・中心(cx,cy)・矩形(bbox) を持つ。Vision の返り値を入れる入れ物。 |
| **[RowCluster](../extractors/raster_extractor.py#L132)** | 「同じ行」とみなした単語の集まり。行のY座標(row_y) と、その行に属する WordBox のリスト(words)。 |
| **ColumnBounds** | 4列の境界のX座標。ヘッダーから推定した「ここより左が1列目、ここから2列目…」の区切り。 |
| **HeaderAnchor** | ヘッダー語群（機器番号/名称/電圧/容量）を満たした行。複数表検出の起点。 |
| **TableCandidate** | 1つの表候補の bbox とヘッダー情報。2パス目OCRの対象単位。 |
| **TableParseResult** | 候補表ごとの抽出結果（行リスト）。 |

**使っているライブラリ（表の「構造」には表用ライブラリは使っていない）**

| 種類 | ライブラリ | 役割 |
|------|------------|------|
| Vision | `google.cloud.vision` | 画像を送って、単語テキスト＋座標の一覧をもらう。 |
| 画像 | Pillow（`PIL.Image`, `ImageDraw`） | 画像の読み込み・切り出し・デバッグ用の枠描画。 |
| 表の組み立て | **なし**（自前ロジック） | 行・列の割り当てやヘッダー／データ行の判定は、すべて raster_extractor 内のコード。pandas や表解析ライブラリは使っていない。 |
| 図面番号補助 | `pdfplumber` | Visionで図面番号を取得できない場合に、PDFテキストレイヤーから図面番号を補助抽出する。 |
| その他 | 標準ライブラリ | `csv`（CSV書き出し）、`re`、`unicodedata`、`dataclasses`、`statistics.median` など。 |

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
   `pdfplumber` で **全ページを順に走査** し、各ページで `page.find_tables()` から表を検出する。  
   **条件**: 幅がページ幅の 40% 以上、かつページ下端 85% より上にある表のみを対象とする。対象表があるページでは **ちょうど2つ** あることを期待し、左からソートして「左側の表」「右側の表」として扱う（[pick_target_tables](../extractors/vector_extractor.py#L190)）。  
   `pick_target_tables` の候補が **0件のページのみ**、`_pick_summary_left_tables` による代替抽出を試す。候補が 1 件または 3 件以上のページは `ValueError` を上げる。
2. **グリッド線の取得**  
   各表の bbox 内で、PDF の線オブジェクトから縦線・横線を収集し、クラスタリングして **セル境界** として使う（[collect_grid_lines](../extractors/vector_extractor.py#L212)）。縦線は 19+1 本（[CELL_COUNT](../extractors/vector_extractor.py#L23) = 19 列）、横線は 4 本以上あることを要求する。これを満たせない場合は `_extract_rows_via_table_cells` にフォールバックする。
3. **表のセル抽出**  
   縦・横の境界を `explicit` で指定して `page.extract_table()` を呼び、行×列の二次元リストを得る（[extract_grid_rows](../extractors/vector_extractor.py#L270)）。1行あたり最大 19 セルまでを正規化して扱う。
4. **ヘッダーの復元**  
   表領域内の単語から、1行目データより上をヘッダーとみなし、グループ行・サブ行・単位行を再構成する（[reconstruct_headers_from_pdf](../extractors/vector_extractor.py#L646)）。「換気機器表」などのタイトルや、最初の機器番号の位置からデータ開始位置を決め、その上をヘッダーとして扱う。
5. **データ行の抽出**  
   先頭列が機器番号パターン（[looks_like_equipment_code](../extractors/vector_extractor.py#L108)、例: `SF-P-1`, `EF-B2-3`）の行をレコードの開始とし、続く行は同じレコードにマージする（[extract_records](../extractors/vector_extractor.py#L780)）。「記記事」「注記事項」等が出てきたらデータ終端とする。
6. **5列CSVの生成**  
   フル表ではなく、**統合用の5列** を切り出す（[build_four_column_rows](../extractors/vector_extractor.py#L989)）。  
   列対応は次のとおり:
   - 機器番号: 元表の 0 列目
   - 名称: 1 列目
   - 動力 (50Hz)_消費電力 (KW): 9 列目
   - 台数: 15 列目
   - 図面番号: ページ単位で抽出した図面番号を各レコードに付与

   ヘッダーは `["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数", "図面番号"]` である。この5列が **vector.csv** として書き出され、unified 統合の「vector 側」入力になる。

<a id="vector-aliases"></a>
#### 5.2.2 列名のゆれへの対応

unified 側では、機器番号・消費電力・台数などの列名が PDF や表によって微妙に異なる場合がある。そのため [unified_csv](../extractors/unified_csv.py) モジュールでは [COLUMN_ALIASES](../extractors/unified_csv.py#L13) で複数の表記を許容している（後述）。

<a id="vector-deps"></a>
#### 5.2.3 依存関係

- **pdfplumber**: PDF のページ・表・線・テキストの取得。外部OCRや Vision API は使わない。

---

<a id="unified"></a>
## 6. 統合処理（Unified CSV）

**モジュール**: [extractors.unified_csv](../extractors/unified_csv.py)

[merge_vector_raster_csv](../extractors/unified_csv.py#L601)(vector_csv_path, raster_csv_path, out_csv_path) が、vector CSV と raster CSV を **機器番号** をキーに結合し、判定列を付与して unified CSV を生成する。

<a id="unified-aliases"></a>
### 6.1 列の対応（エイリアス）

両方のCSVで「実質同じ意味の列」を複数のヘッダー名で受け付ける。

| 正規キー | 許容されるヘッダー名の例（入力 vector / raster CSV 用） |
|----------|--------------------------------------------------------|
| equipment_id | 機器番号, 機械番号 |
| vector_name | 名称, 機器名称 |
| vector_power_per_unit_kw | 動力 (50Hz)_消費電力 (KW), 動力(50Hz)_消費電力(KW), 動力(50Hz)_消費電力(Kw) 等 |
| vector_count | 台数 |
| vector_drawing_number | 図面番号, 図番, 機器表 図面番号 |
| raster_name | 機器名称, 名称 |
| raster_voltage | 電圧(V), 電圧（V） |
| raster_capacity_kw | 容量(kW), 容量(KW), 容量(Kw), 容量（kW） 等 |
| raster_drawing_number | 図面番号, 盤表 図面番号 |

ヘッダーは NFKC 正規化し、空白・全角空白を除いた小文字で比較してマッチさせる（[_normalize_header](../extractors/unified_csv.py#L78)）。機器番号の結合キーは **大文字化・空白除去** した値（[_normalize_key](../extractors/unified_csv.py#L84)）を使う。

<a id="unified-raster"></a>
### 6.2 Raster 側の集約

Raster CSV では、**同じ機器番号** が複数行にまたがることがある（1台あたり1行の記載など）。unified では raster を **機器番号でグループ化** し、次のように扱う。

- マッチした行数: `raster_match_count`（＝盤表にその機器番号が何行あるか）
- 容量(kW): 非空・出現順・重複除去で容量候補を保持する（数値化できる値は正規化した表示値にする）。
- 名称候補: 非空・出現順・重複除去で連結し、`盤表 記載名` に出力する。
- 図面番号: 非空・出現順・重複除去で連結し、`盤表 図面番号` に出力する（例: `E-024,E-031`）。
- 記載トレース: raster の行単位情報（図面番号・名称・容量）を出現順で保持し、同一内容の重複件数を集約する。

<a id="unified-merge"></a>
### 6.3 結合と判定

- **主軸は Vector** である。vector の各行（機器番号）に対して、同じ機器番号の raster 集約結果を1つ紐づける。
- **raster のみ機器**: vector に存在しない機器番号が raster にある場合、その行は統合結果の末尾に追加し、`総合判定=✗`、`判定理由=機器表に記載なし` として出力する。
- **raster の機器ID空欄行**: raster に機器IDが空欄の行がある場合は、照合対象には入れず、名称・容量・図面番号（同一内容は件数集約）ごとに統合結果の末尾へ追加する。`総合判定=要確認`、`判定理由=盤表ID未記載` を設定する。
- **機器表 記載名**: unified の `機器表 記載名` は vector 側の名称列（`vector_name`）を NFKC + 空白除去した値を採用する。
- **機器表 図面番号**: vector 側の図面番号を機器番号単位で集約し、非空・出現順・重複除去で連結して出力する。
- **存在判定（境界）**: 片側に存在しないことが確定した場合は `mismatch`（表示 `✗`）。`盤表ID未記載` など紐付け不能で存在判定自体が確定できない場合は `review`（表示 `要確認`）。
- **台数**: vector の「台数」と raster のマッチ行数を比較し、差分は **台数差** として出力する。
- **台数判定**: `台数判定` は `◯` / `✗` / `要確認` の3値を出力する。比較不能（例: 機器表台数欠損）は `要確認`。
- **容量**: `機器表 消費電力(kW)` には vector の元値（raw）を保持し、`盤表 容量(kW)` は raster 側容量候補を非空・出現順・重複除去でカンマ連結した値を出力する。  
  判定用には `機器表 判定モード` と `機器表 判定採用容量(kW)` を別列で出力し、モード付き容量の根拠は `容量判定補足` に記録する。  
  `容量差(kW)` は **両側が単一数値のときのみ** `盤表 - 機器表` を出力する。  
  **容量判定**: 非数値（例: `10VA`）、カンマ区切り複数候補（例: `0.75,1.5`）、同一IDで複数容量がある場合は `要確認`。  
  ただし機器表容量が `(冷)/(暖)/(低温)` の形式で、機器名称に `冷房専用` / `暖房専用` / `低温専用` がある場合は、対応モードの値を採用して数値比較する。  
  モードを特定できない場合は、まず `(冷)/(暖)/(低温)` の **最大値** を判定採用する。最大値が同値で複数モードにまたがる場合のみ raw のまま `要確認` とする。`EPS_KW`（0.1 kW）は両側単一数値時のみ適用。  
  なお `ME_CHECK_CAPACITY_FALLBACK=strict` の場合は、モード未特定時に最大値採用を行わず `要確認` とする。
- **名称判定**: 正規化後に一致なら `◯`、明確に不一致なら `✗`、片側欠落や取得不能は `要確認`。
- **総合判定**: 例外なしで `review > mismatch > match` の優先順位を適用する（表示値は `要確認 > ✗ > ◯`）。
- **盤表 記載トレース**: 同一機器IDで図面番号・名称・容量の組み合わせが2種類以上ある場合、raster 行単位の `図面:<番号> 名称:<名称> 容量:<値>` を ` || ` 区切りで出力する。  
  同一内容が複数行ある場合は末尾に `xN` を付与する。空欄は `?` として表示する。
- **行粒度**: raster 側は機器ID単位で集約される。一方、統合CSVは vector の各行を主軸に出力するため、vector 側に同一機器IDが複数行ある場合は統合結果も複数行になり得る。
- **判定理由（一本化）**: 旧 `不一致内容/確認理由` 列は廃止。CSVには `判定理由` 1列のみ出力する。  
  1) 総合判定 `◯` の場合は空欄  
  2) 総合判定 `✗` / `要確認` の場合は主原因1文（例: `台数差分=1`, `容量が複数候補`, `盤表ID未記載`）  
  3) 現行ロジックが内部で生成していた旧理由文字列がある場合は、その理由を最優先で採用
  4) 現状実装の理由選定は次の挙動に従う。  
     - `✗` のとき: `存在不一致 > 台数不一致 > 容量不一致 > 名称不一致`  
     - `要確認` のとき: 要確認理由を優先（`台数不明` / `容量が数値でない・複数候補` / `名称不明` など）

<a id="unified-output"></a>
### 6.4 出力列（unified CSV）

unified CSV は **vector の生データ列は含めず**、次の `OUTPUT_COLUMNS` の21列だけを出力する。CSVは **UTF-8 with BOM (`utf-8-sig`)** で出力する。

| 列名 |
|------|
| 総合判定 |
| 台数判定 |
| 容量判定 |
| 名称判定 |
| 判定理由 |
| 機器ID |
| 機器表 記載名 |
| 盤表 記載名 |
| 機器表 台数 |
| 盤表 台数 |
| 台数差 |
| 機器表 消費電力(kW) |
| 機器表 モード容量(kW) |
| 機器表 判定モード |
| 機器表 判定採用容量(kW) |
| 容量判定補足 |
| 盤表 容量(kW) |
| 容量差(kW) |
| 機器表 図面番号 |
| 盤表 図面番号 |
| 盤表 記載トレース |

#### 出力サンプル（新仕様）

```csv
総合判定,台数判定,容量判定,名称判定,判定理由,機器ID,機器表 記載名,盤表 記載名,機器表 台数,盤表 台数,台数差,機器表 消費電力(kW),機器表 モード容量(kW),機器表 判定モード,機器表 判定採用容量(kW),容量判定補足,盤表 容量(kW),容量差(kW),機器表 図面番号,盤表 図面番号,盤表 記載トレース
◯,◯,◯,◯,,PAC-1,空調機/空冷HPパッケージ/マルチタイプ/(冷房専用),空調機/空冷HPパッケージ/マルチタイプ/(冷房専用),1,1,0,"(冷)9.45 / (暖)7.18 / (低温)9.43","冷=9.45,暖=7.18,低温=9.43",冷,9.45,機器名称ヒント(冷房専用)で(冷)を採用,9.45,0,M-03-03,E-025,
◯,◯,◯,◯,,PAC-2,空調機/空冷HPパッケージ/マルチタイプ,空調室外機,1,1,0,"(冷)3.93 / (暖)4.05 / (低温)5.32","冷=3.93,暖=4.05,低温=5.32",最大値(低温),5.32,機器名称からモード特定不可のため最大値を採用,5.32,0,M-03-03,E-025,
```

---

<a id="unified-validation-20260219"></a>
### 6.5 実データ検証メモ（2026-02-19）

- 検証対象: `me-check_照合結果_20260219_0551.csv`（189行・21列）。
- `機器表 消費電力(kW)` が複合表記（`/` を含む）な行では、`機器表 モード容量(kW)` に `冷=... , 暖=... , 低温=...` 形式でパース結果が残る。
- 判定フローは次の順で確認できる。  
  1) 機器名称ヒントがある場合は該当モードを採用（例: PAC-1 は `冷房専用` で `(冷)` を採用）  
  2) ヒントがない場合は最大値モードを採用（例: PAC-6〜PAC-13）  
  3) それでも一致しない場合は `✗`（例: PAC-2, PAC-4, PAC-5, PAC-14）
- 最大値フォールバックにより、従来 `要確認` だった複合表記行の多くが `◯` へ改善した。
- `機器表 モード容量(kW)` は判定の説明責任を担う検証列として有効。提出用表示で見づらい場合は、削除よりも「右端へ寄せる / UIで非表示」を推奨する。
- `容量判定補足`（日本語）で、採用モードと根拠を追跡できることを確認した。

---

<a id="api"></a>
## 7. API・エンドポイント一覧（M-E-Check 関連）

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/me-check` | M-E-Check 用UI（me-check.html）を返す。 |
| POST | `/customer/run` | 機器表PDF・盤表PDFを multipart で受け取り、raster / vector 抽出（既定は並列、`ME_CHECK_PARALLEL_EXTRACT=0` で直列）の後に unified を実行し、結果をHTML（簡易表 or エラー）で返す。 |
| GET | `/jobs/{job_id}/unified.csv` | 指定した unified ジョブの `unified.csv` をダウンロードする（ダウンロード名は `me-check_照合結果_YYYYMMDD_HHMM.csv`）。 |

`/customer/run` のパラメータは `panel_file`（盤表PDF）と `equipment_file`（機器表PDF）の2つが必須。  
拡張子が `.pdf` でない場合はエラーHTMLが返る（現行実装はファイル内容のMIME判定までは行わない）。

---

<a id="develop"></a>
## 8. 開発者向け: Develop ページ

`GET /me-check/develop` で開く **M-E-Check Develop** ページ（`templates/develop.html`）では、M-E-Check の処理を **段階的に** 試せる。

- **Raster**: 盤表PDFを1本だけアップロードし、`POST /raster/upload` で raster.csv を生成。Job ID と CSV ダウンロードリンクが返る。
- **Vector**: 機器表PDFを1本だけアップロードし、`POST /vector/upload` で vector.csv を生成。同様に Job ID とダウンロードリンク。
- **Unified**: Raster と Vector の **既存の Job ID** をフォームで指定し、`POST /unified/merge` で統合のみ実行。unified.csv のダウンロードリンクが得られる。

お客さん向けの `/me-check` は「2ファイルを一度に送って全部やる」フローであり、Develop は「Raster / Vector / Unified を個別に実行して挙動を確認する」ためのコンソールである。

[![Image from Gyazo](https://i.gyazo.com/c8e65fb9233c4d8f9be622297efe286c.jpg)](https://gyazo.com/c8e65fb9233c4d8f9be622297efe286c)
---

<a id="env"></a>
## 9. 環境・設定

- **Raster 抽出**
  - `VISION_SERVICE_ACCOUNT_KEY`: Google Cloud Vision API 用のサービスアカウントJSON（文字列）。未設定だと raster ジョブで `ValueError`。
  - システムに `pdftoppm`（poppler）がインストールされている必要あり。
- **Vector 抽出**
  - 追加の環境変数は不要。pdfplumber が PDF を開ける環境であればよい。
- **Unified 判定**
  - `ME_CHECK_CAPACITY_FALLBACK`: モード未特定時の容量採用方針。`max`（既定: 最大値採用）または `strict`（最大値採用せず要確認）。
- **ジョブ保存先**
  - `/tmp/plan2table/jobs`。本番では永続化やクリーンアップ戦略の検討が必要。

---

[^1]: `pdfplumber` は、PDF 内のテキストや表・罫線の構造を解析して、Python からテキストやテーブルデータを抽出するためのライブラリです。
[^2]: `Google Cloud Vision API` は、画像やPDF内の文字や物体を機械学習モデルで解析し、OCR（テキスト抽出）や物体検出などの画像認識機能をアプリから呼び出せる Google Cloud のAPIサービスです。
[^3]: `pdftoppm` は、Poppler に含まれるコマンドラインツールで、PDF のページを PNG などの画像に変換します。本アプリでは `subprocess` で実行し、対象ページ（アプリ実行時は `page=0` のため全ページ）を画像化しています。
[^4]: `Pillow` は、Python で画像の読み込み・切り抜き・保存などを行う画像処理ライブラリです。本アプリでは pdftoppm が出力した PNG のページ全体OCR・候補領域切り出し・デバッグ描画（および旧ロジックの左右分割）に使っています。
