# E-142 抽出機能 詳細設計（開発者向け）

このドキュメントは `E-142` 抽出ロジックを、実装意図と制約を含めて説明する開発者向け資料です。  
対象実装:

- `extractors/e142_extractor.py`
- `main.py`（E-142ルート/HTML返却）
- `extractors/job_store.py`（`kind="e142"` のCSV名解決）

---

## 1. 目的と前提

`E-142` 系の姿図PDFから、**大枠単位で1行CSV**を生成します。  
出力はヘッダーなしで、1行の基本形は次です。

- `[タイトル, 番号, ラベル1, 値1, ラベル2, 値2, ...]`

運用上の前提:

- 複数ページを処理する（`page=0`）
- 順序は「左上から右、次段へ」を維持する
- 表がない枠も出力する（`[タイトル, 番号]` または `[タイトル]`）
- OCR揺れ（全角半角、誤字、結合ずれ）をある程度吸収する

---

## 2. API / I/O 契約

### 2.1 ルート

- `GET /e-142`: E-142専用画面
- `POST /e-142/upload`: PDFアップロードして抽出
- `GET /jobs/{job_id}/e142.csv`: 結果CSVダウンロード

### 2.2 出力仕様

- ファイル名: `e142.csv`（`job_store.fixed_csv_name(kind="e142")`）
- 文字コード: `UTF-8 with BOM`（`utf-8-sig`）
- ヘッダー: なし
- 列数: 行ごとに可変

### 2.3 HTML表示仕様

`main.py` の `_build_e142_rows_html()` は、CSV各行をそのままカンマ連結して `<ol><li>` で表示します。  
E-055 / E-251 のような見出し付き表ではなく、**1行=1枠**を可視化する形式です。

---

## 3. 抽出パイプライン

エントリポイントは `extract_e142_pdf()` です。

1. Visionクライアント構築（`build_vision_client`）
2. 対象ページ解決（`resolve_target_pages`）
3. `pdftoppm` でページ画像化
4. Vision OCRで単語 + bbox取得（`extract_words`）
5. 行クラスタ + Xギャップ分割でセグメント化（`build_segments_from_words`）
6. 表ブロック単位でタイトル/番号/表行を対応付け（`build_frame_rows_from_segments`）
7. 読み順ソート（`_sort_frame_rows_in_reading_order`）
8. CSV書き出し（`write_e142_csv`）

`extract_e142_pdf()` は返り値として、行数・列数（`column_n`）・処理ページ情報を返します。

---

## 4. データモデル

`extractors/e142_extractor.py` の主データ構造:

- `Segment`
  - OCR単語群を行+X分割した最小処理単位
  - `page`, `row_y`, `x0/x1`, `top/bottom`, `text_compact` を保持
- `TableBlock`
  - 表候補セグメント群をまとめたブロック
- `ParsedTableBlock`
  - `TableBlock` + 抽出済み `pairs` + `label_count`
- `FrameRow`
  - 最終1行（`title`, `code`, `pairs`）
  - `values` プロパティでCSV配列へ展開

---

## 5. 主要アルゴリズム

### 5.1 セグメント化

`build_segments_from_words()`:

- `cluster_by_y` で行クラスタ
- `_split_row_cluster_by_x_gap` で行内をXギャップ分割
- `text_compact` は NFKC + 空白除去で比較用に統一

`extract_e142_pdf()` では2系統のセグメントを作成します。

- 本処理用: `x_gap=70.0`
- タイトル精密用: `x_gap=40.0`（`TITLE_SEGMENT_X_GAP`）

後者は、タイトル帯の過結合を抑えるためです。

### 5.2 表ブロック抽出

`_is_table_segment()` が `LABEL_KEYWORDS` を含むセグメントを表候補とします。  
`_cluster_table_segments()` が `x重なり + y近接` で `TableBlock` に統合します。

### 5.3 タイトル候補抽出と絞り込み

`_is_title_candidate()` で次を除外します。

- 型番らしき文字列
- `商品コード` / `特注品`
- 表ラベル語・寸法語・注記語
- 記号だけ、数値単位だけ、先頭記号のみ など

さらに `_filter_title_candidates_by_header_rows()` で、  
コード行中心（`_header_row_centers_from_codes`）とのY関係を使って候補を絞ります。

### 5.4 タイトル割当と分割

`_pick_title_for_block()` が「表ブロック直上」の候補をスコア選択します。  
1つのタイトルセグメントが複数ブロックをまたぐ場合は、`_split_title_text_by_blocks()` で分割します。

この分割では:

- ブロック中心の相対位置で切り出し境界を算出
- `_snap_split_boundary()` でキーワード境界へ寄せる
- 分割品質が低い場合は保守的にフォールバック

### 5.5 番号（識別子）抽出

`_find_code_in_segment()` は次の順で識別子を検出します。

1. 型番パターン（例: `MC-N0190`）
2. 括弧付き商品コード（例: `(商品コード:4361000)`）
3. 商品コード（例: `商品コード:4361000`）
4. 特殊識別子（`特注品`）

`_pick_code_for_title()` がタイトル帯直下の候補から、X/Y距離とペナルティで最適化します。  
商品コードは通常型番より閾値を緩めています（`PRODUCT_CODE_ASSIGN_MAX_SCORE`）。

表がないケース用に `_pick_code_for_anchor()` も実装し、  
タイトル位置をアンカーに近傍候補を拾います。

### 5.6 ラベル値ペア抽出

`extract_label_value_pairs()`:

- ラベル語の出現位置を全走査
- 重なりを除去しつつ `(label, value)` を作成
- 重複ラベルや空値をマージ

`_extract_pairs_from_block()`:

- セグメント単位でペア抽出
- ラベルなし継続文を直前値に連結（`_is_continuation_text`）

OCR揺れ吸収として `_normalize_for_label_detection()` と `_clean_value()` で、
`形備 -> 形状`、`黑 -> 黒` などを補正します。

### 5.7 外れ値・右側大枠抑制

`_filter_extreme_wide_blocks()` は、幅中央値に対して極端に広いブロックを除外します。  
これにより、同サイズ枠群を主対象とし、外れ大枠の混入を抑えます。

### 5.8 取付参考例の正規化

`_refine_titles_for_reference_rows()` で、取付参考系行を後処理します。

- `取付参考例` を含むタイトル行は `code/pairs` を強制クリア
- 近傍兄弟行（例: マグネットセンサー本体）からタイトル補完
- 最終的に `マグネットセンサー（露出型）取付参考例` のようなタイトル単独行に正規化

---

## 6. 行生成ルール（仕様）

`FrameRow.values` の展開ルール:

1. `title` があれば先頭に追加
2. `code` があれば2列目に追加
3. `pairs` を `label, value` の順でフラット追加

結果として:

- 表あり: `[タイトル, 番号, ラベル1, 値1, ...]`
- 表なし + 番号あり: `[タイトル, 番号]`
- 表なし + 番号なし: `[タイトル]`

`_refine_titles_for_reference_rows()` の条件を満たす行は、最終的にタイトル単独になります。

---

## 7. 並び順・重複除去

`_sort_frame_rows_in_reading_order()`:

- ページ単位で処理
- 近いYを同一バンドとしてグルーピング（`READING_ORDER_Y_BAND`）
- 各バンド内で `x0` 昇順

これにより「左→右、次段へ」の読み順を近似再現します。  
その後、`(page, tuple(values))` で重複行を除去します。

---

## 8. ルート実装とメタデータ

`main.py`:

- `_run_e142_job()` が `extract_e142_pdf()` を実行
- `debug_dir` は `/tmp/plan2table/debug/<job_id>`
- 行数・列数は `_csv_profile_no_header()` で算出
- `metadata.json` に `extract_result` を保存

`job_store.py`:

- `kind="e142"` を `e142.csv` に固定解決

---

## 9. テスト戦略（現実装）

主に次で担保しています。

- `tests/test_e142_extractor.py`
  - タイトル/番号抽出
  - 表ラベル値抽出
  - 表なし/番号なし分岐
  - 左上記号除外
  - 読み順
  - `特注品`、`(商品コード:xxxx)`、`塗装` 行
  - 取付参考例のタイトル単独化
- `tests/test_integration_routes.py`
  - `/e-142/upload` 成功/失敗
  - `data-kind="e142"` とDLパス
  - `/jobs/{job_id}/e142.csv` ダウンロード
- `tests/test_job_store.py`
  - `kind="e142"` の固定ファイル名

---

## 10. 調整ポイント（チューニング）

抽出調整で主に触る値:

- `y_cluster`（行クラスタ密度）
- `x_gap`（セグメント分割）
- `TITLE_SEGMENT_X_GAP`（タイトル専用分割）
- `CODE_ASSIGN_MAX_SCORE` / `PRODUCT_CODE_ASSIGN_MAX_SCORE`
- `TABLE_MAX_WIDTH_RATIO`（外れ大枠除外）
- `READING_ORDER_Y_BAND`（読み順バンド）

変更時は必ず `tests/test_e142_extractor.py` と実PDFで回帰確認してください。

---

## 11. 既知の制約

1. ヒューリスティック依存
- 罫線の完全ベクトルトレースではなく、座標/キーワード中心の推定です。

2. 表ラベル語依存
- ラベル語辞書外の表現は表として拾えないことがあります。

3. レイアウト差分耐性
- タイトル帯・番号帯・表帯の相対位置が大きく崩れると精度が落ちます。

4. OCR品質依存
- 解像度不足や文字潰れの場合は、分割・割当ともに不安定になります。

---

## 12. 関連ドキュメント

- 概要: `docs/e-142.md`
- FAQ: `docs/e142-faq.md`
- 実装: `extractors/e142_extractor.py`, `main.py`
- テスト: `tests/test_e142_extractor.py`, `tests/test_integration_routes.py`
