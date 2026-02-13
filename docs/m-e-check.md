# M-E-Check 機能ドキュメント

## 1. 概要

**M-E-Check**（Machine–Equipment Check）は、本アプリが提供する2大機能のうちの1つである。**機器表PDF**（換気・空調等の機器一覧表）と**電力制御盤表PDF**（盤表）の2種類のPDFを入力とし、それぞれから表データを抽出したうえで**機器番号をキーに統合**し、機器表の記載と盤表の記載が一致しているかを判定する。結果は **HTML上の簡易表** で確認でき、**CSV** で本格的なデータとしてダウンロードできる。

### 1.1 用語

| 用語 | 説明 |
|------|------|
| **機器表** | 換気機器表など、機器番号・名称・消費電力・台数などを記載した表。PDFは**ベクトル（テキスト/表線）**で構成されることが多い。 |
| **盤表** | 電力制御盤の機器一覧。機器番号・機器名称・電圧・容量(kW)などを記載。PDFは**ラスター（スキャン画像）**である場合がある。 |
| **Vector** | 機器表PDFから **pdfplumber**[^1] で表構造を検出し抽出したCSV。内部では「vector」ジョブとして扱う。 |
| **Raster** | 盤表PDFを **pdftoppm**[^3] と **Pillow**[^4] で画像化し、**Google Cloud Vision API**[^2] でOCRして、4列表として抽出したCSV。内部では「raster」ジョブとして扱う。 |
| **Unified** | Vector CSV と Raster CSV を **機器番号で結合** し、存在・台数・容量の一致判定を付与した統合CSV。 |

### 1.2 入力・出力

- **入力**
  - **機器表PDF**（`equipment_file`）: 換気機器表など、表形式のPDF。1ページ目に2つの横並び表がある想定。
  - **盤表PDF**（`panel_file`）: 電力制御盤表。1ページ目を画像化してOCRする想定。
- **出力**
  - **HTML**: 判定結果の主要列（総合判定・機器番号・機器名・kW・台数・盤台数・容量差・不一致理由）を簡易表示。
  - **CSV**（`unified.csv`）: 統合結果の全列。ダウンロード用エンドポイント `GET /jobs/{job_id}/unified.csv` で取得。

---

## 2. ユーザー向け機能（UI）

### 2.1 ページとルート

- **`GET /me-check`**  
  M-E-Check 用の単一ページを表示する。テンプレートは `templates/me-check.html`。

### 2.2 画面の流れ

1. 画面上で **機器表PDF** と **盤表PDF** の2つのファイルを選択（ドラッグ＆ドロップまたはファイル選択）。
2. 「実行する」ボタンで送信すると、フォームは **`POST /customer/run`** に `multipart/form-data` で送信される（HTMX の `hx-post` 使用）。
3. 処理中は `#customer-loading` が表示され、「PDFを処理中です…」と表示される。
4. 成功時: `#customer-result` に統合結果の **簡易HTML表** が差し替えられ、CSVダウンロードリンクが表示される。
5. 失敗時: `#customer-result` に **エラー用HTML**（stage と message）が返る。

### 2.3 簡易表に表示される列

`main.py` の `CUSTOMER_TABLE_COLUMNS` で定義されている。表示ラベルと、unified CSV の列名の対応は以下のとおりである。

| 表示ラベル | 対応する unified CSV の列（候補のいずれか） |
|------------|---------------------------------------------|
| 判定(◯/✗) | 総合判定(○/×) 等 |
| 機器番号   | 機器番号 |
| 機器名     | 名称 |
| 機器kW     | vector_消費電力(kW)_per_unit |
| 機器台数   | vector_台数_numeric |
| 盤台数     | raster_台数_calc |
| 合計差(kW) | 容量差分(kW) |
| 理由       | 不一致理由 |

---

## 3. 内部処理の全体像

### 3.1 処理の3段階

`handle_customer_run`（`POST /customer/run`）では、次の3段階が **同期的に** 実行される。

```
機器表PDF (equipment_file)  ──→ Vector抽出  ──→ vector.csv (vector job)
盤表PDF   (panel_file)      ──→ Raster抽出 ──→ raster.csv (raster job)
                                                      │
                                                      ▼
                              Unified統合  ←── vector.csv + raster.csv
                                                      │
                                                      ▼
                                               unified.csv (unified job)
                                                      │
                                                      ├──→ HTML簡易表
                                                      └──→ CSVダウンロード
```

1. **Panel → Raster**  
   `panel_file` のバイト列を `_run_raster_job` に渡す。盤表PDFを画像化し、Vision API でOCRして **raster.csv** を生成し、raster ジョブとして保存する。
2. **Equipment → Vector**  
   `equipment_file` のバイト列を `_run_vector_job` に渡す。機器表PDFから pdfplumber で表を抽出し、**vector.csv**（4列）を生成し、vector ジョブとして保存する。
3. **Unified**  
   `_run_unified_job(raster_job_id, vector_job_id)` で、既存の raster.csv と vector.csv を読み、`extractors.unified_csv.merge_vector_raster_csv` により **unified.csv** を生成する。結果は unified ジョブとして保存され、その `job_id` で CSV ダウンロードと簡易表の表示に使われる。

いずれかの段階で例外が発生した場合は、その時点で **エラー用HTML** が返り、以降の段階は実行されない。エラー時の `stage` は `panel->raster` / `equipment->vector` / `unified` のいずれかである。

### 3.2 ジョブストア

- ジョブは `extractors.job_store` で管理される。
- ルートディレクトリは `JOBS_ROOT = Path("/tmp/plan2table/jobs")`。
- 各ジョブは UUID v4 の `job_id` に対応するディレクトリ `JOBS_ROOT / job_id` を持ち、その中に以下が保存される。
  - **raster**: `input.pdf`, `raster.csv`, `debug/`（デバッグ画像）, `metadata.json`
  - **vector**: `input.pdf`, `vector.csv`, `metadata.json`
  - **unified**: `unified.csv`, `metadata.json`（`source_job_ids` で raster/vector の job_id を参照）

CSV の実体ファイル名は kind に応じて `raster.csv` / `vector.csv` / `unified.csv` で固定である。

---

## 4. PDF から CSV への抽出

### 4.1 盤表PDF → Raster CSV（raster_extractor）

**モジュール**: `extractors.raster_extractor`

盤表PDFは「画像として扱う」前提である。スキャンされたPDFや、テキストが画像として埋め込まれているPDFを想定している。

#### 4.1.1 処理の流れ

1. **PDF → 画像**  
   `pdftoppm` で指定ページ（デフォルト1ページ目）を PNG に変換する（DPI はデフォルト 300）。`run_pdftoppm` がこれを実行する。
2. **左右分割**  
   画像を縦の中央で **左(L) と 右(R)** に分割する（`SIDE_SPLITS`）。盤表が2段や2列に分かれているレイアウトに対応する。
3. **OCR**  
   各サイド画像を **Google Cloud Vision API** の `document_text_detection` に送り、単語単位のテキストとバウンディングボックスを取得する（`extract_words`）。
4. **行クラスタリング**  
   Y座標に基づき単語を **行** にグループ化する（`cluster_by_y`）。しきい値は `y_cluster`（デフォルト 20.0 px）。
5. **列境界の推定**  
   ヘッダー行らしき行から「機器番号」「機器名称」「電圧(V)」「容量(kW)」の4列の境界（`ColumnBounds`）を推定する（`infer_column_bounds`）。ヘッダーキーワード（機器・記号・名称・電圧・容量など）のスコアでヘッダー行を選び、その単語のX座標から列の区切りを決める。
6. **データ行の抽出**  
   ヘッダーより下の領域（`DATA_START_OFFSET` 以降）の単語を、列境界に従って4列に割り当て、行ごとにまとめる（`rows_from_words`）。  
   ヘッダー行・フッター行はキーワードで除外し、**データ行** のみを残す（`is_data_row`）。機器番号のパターン（例: `[A-Z]{1,4}-[A-Z0-9]{1,6}`）や、名称に含まれるキーワード（ポンプ・排風・送風など）でデータ行を判定する。
7. **セル正規化**  
   機器番号と名称の混入補正、単位表記の統一（例: 1/200 → 1φ200）、名称の表記ゆれ（湧水ポンプ→清水ポンプ）などを `normalize_row_cells` で行う。
8. **CSV出力**  
   列は `OUTPUT_COLUMNS = ["機器番号", "機器名称", "電圧(V)", "容量(kW)"]` の4列。左右両サイドの行をまとめて `raster.csv` に書き出す。デバッグ用に `debug_dir` へ列境界や単語ボックスを描画した画像を保存する。

#### 4.1.2 依存関係

- **poppler-utils** の `pdftoppm`（PDF→PNG）
- **Google Cloud Vision API**（`google-cloud-vision`）。認証は `VISION_SERVICE_ACCOUNT_KEY` 環境変数にサービスアカウントJSONを渡して行う。
- **Pillow**（画像の分割・デバッグ描画）

Raster 抽出が有効に動くには、アプリ起動時に `VISION_SERVICE_ACCOUNT_KEY` が設定されている必要がある。未設定の場合、`_run_raster_job` 内で `ValueError` が発生する。

#### 4.1.3 Vision API の結果の整理（表にするまで）

Vision API は「この画像にどんな文字が、どこにあったか」を **単語ごと** に返すだけである。**行・列の区切りは返してくれない**。そのため、「どの単語が何行目の何列目か」を **アプリ側で決めて**、4列の表（raster.csv）に組み立てている。

**Vision API が返すもの と 最終的に欲しいもの**

| Vision API が返すもの | 最終的に欲しいもの（raster.csv） |
|----------------------|----------------------------------|
| 単語のテキスト ＋ その単語の矩形座標（bounding_box）の羅列 | 行ごとに「機器番号」「機器名称」「電圧(V)」「容量(kW)」の4列が並んだ表 |

**整理の流れ（何をしているか）**

| 段階 | やっていること | コード上の主なもの |
|------|----------------|---------------------|
| 1. 単語をまとめる | 返ってきた単語に、中心座標（cx, cy）と矩形（bbox）を付けて **WordBox** として保持 | `extract_words` → `WordBox`（dataclass） |
| 2. 行に分ける | Y座標が近い単語を「同じ行」として **RowCluster** にまとめる | `cluster_by_y` → `RowCluster`（dataclass） |
| 3. 列を決める | ヘッダー行の単語のX座標から、4列の境界 **ColumnBounds** を推定 | `infer_column_bounds` → `ColumnBounds`（dataclass） |
| 4. セルに割り当て | 各行の単語を、X座標で列境界と照らして「機器番号列」「名称列」… に振り分け | `assign_column`, `rows_from_words` |
| 5. 表として出力 | データ行だけ残し、表記ゆれを補正してから **csv** で書き出し | `normalize_row_cells`, `write_csv`（標準ライブラリ） |

**使っている型（すべて raster_extractor 内の dataclass）**

| 型名 | 役割 |
|------|------|
| **WordBox** | 単語1つ分。テキスト・中心(cx,cy)・矩形(bbox) を持つ。Vision の返り値を入れる入れ物。 |
| **RowCluster** | 「同じ行」とみなした単語の集まり。行のY座標(row_y) と、その行に属する WordBox のリスト(words)。 |
| **ColumnBounds** | 4列の境界のX座標。ヘッダーから推定した「ここより左が1列目、ここから2列目…」の区切り。 |

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

### 4.2 機器表PDF → Vector CSV（vector_extractor）

**モジュール**: `extractors.vector_extractor`

機器表PDFは **ベクトル（テキスト・線）** で構成されている前提である。表の罫線やセルが PDF の描画命令として存在し、pdfplumber で表として検出できる形式を想定している。

#### 4.2.1 処理の流れ

1. **表の検出**  
   `pdfplumber` で1ページ目を開き、`page.find_tables()` で表を検出する。  
   **条件**: 幅がページ幅の 40% 以上、かつページ下端 85% より上にある表のみを対象とする。この条件を満たす表が **ちょうど2つ** あることを期待し、左からソートして「左側の表」「右側の表」として扱う（`pick_target_tables`）。2つでない場合は `ValueError` を上げる。
2. **グリッド線の取得**  
   各表の bbox 内で、PDF の線オブジェクトから縦線・横線を収集し、クラスタリングして **セル境界** として使う（`collect_grid_lines`）。縦線は 19+1 本（`CELL_COUNT = 19` 列）、横線は 4 本以上あることを要求する。
3. **表のセル抽出**  
   縦・横の境界を `explicit` で指定して `page.extract_table()` を呼び、行×列の二次元リストを得る（`extract_grid_rows`）。1行あたり最大 19 セルまでを正規化して扱う。
4. **ヘッダーの復元**  
   表領域内の単語から、1行目データより上をヘッダーとみなし、グループ行・サブ行・単位行を再構成する（`reconstruct_headers_from_pdf`）。「換気機器表」などのタイトルや、最初の機器番号の位置からデータ開始位置を決め、その上をヘッダーとして扱う。
5. **データ行の抽出**  
   先頭列が機器番号パターン（`looks_like_equipment_code`、例: `SF-P-1`, `EF-B2-3`）の行をレコードの開始とし、続く行は同じレコードにマージする（`extract_records`）。「記記事」「注記事項」等が出てきたらデータ終端とする。
6. **4列CSVの生成**  
   フル表ではなく、**統合用の4列** だけを切り出す（`build_four_column_rows`）。  
   列対応は次のとおり:
   - 機器番号: 元表の 0 列目
   - 名称: 1 列目
   - 動力 (50Hz)_消費電力 (KW): 9 列目
   - 台数: 15 列目  

   ヘッダーは `["機器番号", "名称", "動力 (50Hz)_消費電力 (KW)", "台数"]` である。この4列だけが **vector.csv** として書き出され、unified 統合の「vector 側」入力になる。

#### 4.2.2 列名のゆれへの対応

unified 側では、機器番号・消費電力・台数などの列名が PDF や表によって微妙に異なる場合がある。そのため `unified_csv` モジュールでは **COLUMN_ALIASES** で複数の表記を許容している（後述）。

#### 4.2.3 依存関係

- **pdfplumber**: PDF のページ・表・線・テキストの取得。外部OCRや Vision API は使わない。

---

## 5. 統合処理（Unified CSV）

**モジュール**: `extractors.unified_csv`

`merge_vector_raster_csv(vector_csv_path, raster_csv_path, out_csv_path)` が、vector CSV と raster CSV を **機器番号** をキーに結合し、判定列を付与して unified CSV を生成する。

### 5.1 列の対応（エイリアス）

両方のCSVで「実質同じ意味の列」を複数のヘッダー名で受け付ける。

| 正規キー | 許容されるヘッダー名の例 |
|----------|---------------------------|
| equipment_id | 機器番号, 機械番号 |
| vector_power_per_unit_kw | 動力 (50Hz)_消費電力 (KW), 動力(50Hz)_消費電力(KW), 動力(50Hz)_消費電力(Kw) 等 |
| vector_count | 台数 |
| raster_name | 機器名称, 名称 |
| raster_voltage | 電圧(V), 電圧（V） |
| raster_capacity_kw | 容量(kW), 容量(KW), 容量(Kw), 容量（kW） 等 |

ヘッダーは NFKC 正規化し、空白・全角空白を除いた小文字で比較してマッチさせる（`_normalize_header`）。機器番号の結合キーは **大文字化・空白除去** した値（`_normalize_key`）を使う。

### 5.2 Raster 側の集約

Raster CSV では、**同じ機器番号** が複数行にまたがることがある（1台あたり1行の記載など）。unified では raster を **機器番号でグループ化** し、次のように集約する。

- 機器名称: ユニークな値を `" / "` で連結
- 電圧(V): 同様に連結
- 容量(kW): 各セルの値を連結し、さらに **数値として解釈できるものは合計**（`raster_容量(kW)_sum`）
- マッチした行数: `raster_match_count`（＝盤表にその機器番号が何行あるか）

### 5.3 結合と判定

- **主軸は Vector** である。vector の各行（機器番号）に対して、同じ機器番号の raster 集約結果を1つ紐づける。
- **存在判定**: その機器番号が raster に1行以上あるか。なければ「盤表に記載なし」。
- **台数**: vector の「台数」と raster の `raster_match_count` を比較。一致で ○、不一致で ×。差分は `台数差分`。
- **容量**: vector 側は `消費電力(kW)/台 × 台数` で **vector_容量(kW)_calc** を計算。raster 側は `raster_容量(kW)_sum`。その差が **容量差分(kW)**。  
  **容量判定**: 容量差分の絶対値が `EPS_KW`（0.1 kW）以下なら ○、それ以外は ×。どちらかが欠損している場合は「容量欠損」として不一致理由に入れる。
- **総合判定**: 存在 ○ かつ 台数 ○ かつ 容量 ○ のときのみ ○。それ以外は ×。
- **不一致理由**: 総合が × のとき、「盤表に記載なし」「台数差分=…」「容量差分=…」「容量欠損」のいずれかを `不一致理由` に設定する。

### 5.4 出力列

unified CSV の列は、**vector の全列** に加え、次の **APPENDED_COLUMNS** が付く。

- raster_機器名称
- raster_電圧(V)
- raster_容量(kW)_values
- raster_容量(kW)_sum
- raster_match_count
- raster_台数_calc
- vector_消費電力(kW)_per_unit
- vector_台数_numeric
- vector_容量(kW)_calc
- 容量差分(kW)
- 台数差分
- 存在判定(○/×)
- 台数判定(○/×)
- 容量判定(○/×)
- 総合判定(○/×)
- 不一致理由

---

## 6. API・エンドポイント一覧（M-E-Check 関連）

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/me-check` | M-E-Check 用UI（me-check.html）を返す。 |
| POST | `/customer/run` | 機器表PDF・盤表PDFを multipart で受け取り、raster → vector → unified の順で実行し、結果をHTML（簡易表 or エラー）で返す。 |
| GET | `/jobs/{job_id}/unified.csv` | 指定した unified ジョブの `unified.csv` をダウンロードする。 |

`/customer/run` のパラメータは `panel_file`（盤表PDF）と `equipment_file`（機器表PDF）の2つが必須。いずれも PDF でない場合はエラーHTMLが返る。

---

## 7. 開発者向け: Develop ページ

`GET /develop` で開く **M-E-Check Develop** ページ（`templates/develop.html`）では、M-E-Check の処理を **段階的に** 試せる。

- **Raster**: 盤表PDFを1本だけアップロードし、`POST /raster/upload` で raster.csv を生成。Job ID と CSV ダウンロードリンクが返る。
- **Vector**: 機器表PDFを1本だけアップロードし、`POST /vector/upload` で vector.csv を生成。同様に Job ID とダウンロードリンク。
- **Unified**: Raster と Vector の **既存の Job ID** をフォームで指定し、`POST /unified/merge` で統合のみ実行。unified.csv のダウンロードリンクが得られる。

お客さん向けの `/me-check` は「2ファイルを一度に送って全部やる」フローであり、Develop は「Raster / Vector / Unified を個別に実行して挙動を確認する」ためのコンソールである。

---

## 8. 環境・設定

- **Raster 抽出**
  - `VISION_SERVICE_ACCOUNT_KEY`: Google Cloud Vision API 用のサービスアカウントJSON（文字列）。未設定だと raster ジョブで `ValueError`。
  - システムに `pdftoppm`（poppler）がインストールされている必要あり。
- **Vector 抽出**
  - 追加の環境変数は不要。pdfplumber が PDF を開ける環境であればよい。
- **ジョブ保存先**
  - `/tmp/plan2table/jobs`。本番では永続化やクリーンアップ戦略の検討が必要。

---

## 9. まとめ

M-E-Check は、

1. **機器表PDF** → pdfplumber で表を検出し、機器番号・名称・消費電力・台数の4列 **vector.csv** を生成、
2. **盤表PDF** → 画像化して Vision API でOCRし、機器番号・機器名称・電圧・容量の4列 **raster.csv** を生成、
3. 両方を **機器番号で結合** し、存在・台数・容量の一致を判定した **unified.csv** を出力、

する一連のパイプラインである。ユーザーは `/me-check` で2つのPDFを送るだけで、HTML上で簡易結果を確認し、unified.csv をダウンロードして詳細な照合・検証に利用できる。


[^1]: `pdfplumber` は、PDF 内のテキストや表・罫線の構造を解析して、Python からテキストやテーブルデータを抽出するためのライブラリです。
[^2]: `Google Cloud Vision API` は、画像やPDF内の文字や物体を機械学習モデルで解析し、OCR（テキスト抽出）や物体検出などの画像認識機能をアプリから呼び出せる Google Cloud のAPIサービスです。
[^3]: `pdftoppm` は、Poppler に含まれるコマンドラインツールで、PDF のページを PNG などの画像に変換します。本アプリでは `subprocess` で実行し、盤表PDFの1ページ目を画像化しています。
[^4]: `Pillow` は、Python で画像の読み込み・切り抜き・保存などを行う画像処理ライブラリです。本アプリでは pdftoppm が出力した PNG を開き、左右に分割するために使っています。