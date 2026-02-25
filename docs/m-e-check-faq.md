# M-E-Check FAQ（実装準拠）

このFAQは、`main.py` と抽出器実装（`extractors/`）に基づいて記載しています。  
推測ではなく、現在コードで実際に行っている処理だけを対象にしています。

---

## 1. 基本フロー

### Q. M-E-Check は2つのPDFをどう扱っていますか？
A. `POST /customer/run` で受けた2ファイルを、次の固定ルートで処理しています（`main.py`）。

1. `panel_file`（盤表PDF） -> Raster抽出（`_run_raster_job`）
2. `equipment_file`（機器表PDF） -> Vector抽出（`_run_vector_job`）
3. Raster CSV と Vector CSV を Unified で照合（`_run_unified_job`）

### Q. 並列実行ですか？
A. 既定で並列です。`ME_CHECK_PARALLEL_EXTRACT=1`（既定）なら Raster/Vector を `asyncio.gather` で並列実行し、`0` で直列実行します（`main.py`）。

---

## 2. 「読み取るべき情報」の認識（全体）

### Q. どの情報を「読むべき情報」として認識していますか？
A. 段階ごとに認識対象を固定しています。

- Vector（機器表PDF）: `機器番号 / 名称 / 動力(50Hz)_消費電力(KW) / 台数 / 図面番号`
- Raster（盤表PDF）: `機器番号 / 機器名称 / 電圧(V) / 容量(kW) / 図面番号`
- Unified（照合）: 機器番号キーで結合し、`台数/容量/名称/ID存在` を判定

認識対象の列ヘッダは `extractors/unified_csv.py` の `COLUMN_ALIASES` で吸収します。

### Q. つまり、どこまでがOCRで、どこからがアプリ判定ですか？
A. RasterはOCR（Vision API）で単語と座標を取り、行/列への再構成はアプリ側ロジックです。  
Vectorはpdfplumberの表抽出を使い、必要列の解決・継続行連結はアプリ側ロジックです。  
最終判定（◯/✗/要確認）はUnifiedロジック（`unified_csv.py`）で行います。

---

## 3. Vector側（機器表PDF）の認識

### Q. 機器表PDFで「対象の表」をどう見つけていますか？
A. まず `pick_target_tables` で、ページ上部の横長テーブルを候補化します（`vector_extractor.py`）。

- テーブル幅がページ幅の40%以上
- 下端がページ高さの85%より上
- 候補が2表であることを期待（2表でない場合は例外）

これで拾えない場合は `_pick_summary_left_tables` にフォールバックし、要約左表を条件付きで抽出します。

### Q. 列はどう確定していますか？
A. 第1優先は罫線ベースです。

1. `collect_grid_lines` で縦横線を集計
2. `extract_grid_rows` で明示線（explicit lines）を使って表抽出

ここで崩れたら `_extract_rows_via_table_cells` にフォールバックし、ヘッダ語（`機器番号/名称/消費電力/台数`）から列インデックスを再決定します。

### Q. 継続行や複数行レコードはどう扱いますか？
A. `extract_records` が先頭列の機器番号パターンを起点に1レコードへ連結します。

- 先頭列が機器番号パターンなら新規レコード
- 同じ機器番号が連続する場合は継続としてマージ
- 注記行マーカー（`記 事` や `■`）以降は停止

---

## 4. Raster側（盤表PDF）の認識

### Q. 盤表PDFで「どこが表か」をどう見つけていますか？
A. `detect_header_anchors` でヘッダ語群を検出し、`detect_table_candidates_from_page_words` で表候補bboxを生成します（`raster_extractor.py`）。

ヘッダ語はカテゴリ化して判定します。

- `code`: 機器番号/記号
- `name`: 名称
- `voltage`: 電圧/V
- `power`: 容量/kW

4カテゴリ中3カテゴリ以上で表ヘッダ候補とみなします。

### Q. 行と列はどう作っていますか？
A. 候補ごとに再OCRし、次で再構成します。

1. `infer_column_bounds` で4列境界（機器番号/名称/電圧/容量）を推定
2. `cluster_by_y` でY近傍単語を1行クラスタ化
3. `assign_column` でX座標から列へ割り当て
4. `normalize_row_cells` でOCRノイズ補正
5. `is_data_row` で採用可否を判定

### Q. 不要行はどう除外していますか？
A. 次を除外します（`is_header_row`, `is_footer_row`, `is_data_row`）。

- ヘッダ行（ヘッダ語多数）
- フッタ行（図面/縮尺/設計など）
- コードだけで名称・電圧・容量がない行
- 名称だけで数値情報がない行

---

## 5. 図面番号の認識

### Q. 図面番号はどう認識していますか？
A. Raster/Vectorで別実装です。

- Raster: まずOCR単語群から `図面番号` ラベル近傍を探索（`extract_drawing_number_from_word_boxes`）。
  見つからないときはPDFテキストレイヤー抽出へフォールバック（`extract_drawing_number_from_text_layer`）。
- Vector: ページテキストから `図面番号` 行を優先探索し、なければ右下領域単語から候補抽出（`extract_drawing_number_from_page`）。

---

## 6. Unified側（照合）の認識

### Q. 何をキーに突合していますか？
A. 機器番号です。`_normalize_key` で NFKC正規化・空白除去・大文字化したキーを使って照合します（`unified_csv.py`）。

### Q. 判定（◯/✗/要確認）はどう決まりますか？
A. `exists(存在)`, `quantity(台数)`, `capacity(容量)`, `name(名称)` の4判定を作り、`_aggregate_judgments` で総合判定します。

- `review` が1つでもあれば総合は `要確認`
- `review` がなく `mismatch` があれば `✗`
- それ以外は `◯`

### Q. 容量判定はどこまで自動解釈しますか？
A. `unified_csv.py` 側で次を実施します。

- 単一数値はそのまま採用
- `(冷)/(暖)/(低温)` 形式はモード別容量として抽出
- 機器名称ヒント（例: 冷房専用）でモード選択を試行
- 決めきれない場合は `ME_CHECK_CAPACITY_FALLBACK` に従う
  - 既定: `max`（最大モード値）
  - `strict`: 未確定のまま

容量差判定は `EPS_KW = 0.1` 以内を一致としています。

### Q. 盤表側に同一機器IDが複数行あるときは？
A. Raster側でIDごとに集約し、件数・名称候補・容量候補・図面番号候補を保持して判定します。  
図面/名称/容量が複数パターンある場合は `盤表 記載トレース` 列に残します（`_format_trace_rows`）。

### Q. 盤表で機器IDが空欄の行はどう扱いますか？
A. Rasterの「IDなし集約」（`raster_missing_id_agg`）に入り、Unifiedでは `判定理由=盤表ID未記載` の `要確認` 行として出力します。

---

## 7. よくある誤解

### Q. M-E-Check は罫線認識だけで全部読んでいますか？
A. いいえ。Vectorは罫線抽出を優先しますが、崩れた場合はヘッダ語ベース抽出へフォールバックします。  
RasterはOCR単語座標からヘッダ語・列境界を推定する方式で、表構造はアプリ側で再構築しています。

### Q. 判定理由はランダムですか？
A. いいえ。`_build_legacy_reason` と `_pick_reason` で、台数・容量・名称・存在判定の結果から機械的に生成しています。

---

## 8. 関連ファイル

- 入口ルート: `main.py` (`/customer/run`, `_run_raster_job`, `_run_vector_job`, `_run_unified_job`)
- Raster抽出: `extractors/raster_extractor.py`
- Vector抽出: `extractors/vector_extractor.py`
- Unified判定: `extractors/unified_csv.py`
- 仕様詳細: `docs/m-e-check-detail.md`
