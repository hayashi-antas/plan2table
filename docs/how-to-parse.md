# How To Parse（境界信号ベースの読み取り設計）

このドキュメントは、このアプリがPDFから情報を読むときに使っている  
**「境界信号」** の考え方を、実装ベースで説明する技術メモです。

---

## 0. 先に結論


- このアプリは表の境界信号で読み取る
- 境界信号には 実線（罫線） と 疑似線（座標クラスタ） の2種類がある
- vector_extractor（M-Eの機器表側）は実線を使う
- raster_extractor / e251_extractor は主に疑似線、e055_extractor は通常疑似線で低信頼時のみ線認識補助

---

## 1. 境界信号とは何か

このアプリでいう「境界信号」は、  
「この文字がどの行・どの列・どの枠に属するか」を決めるための手掛かりです。

境界信号は2系統あります。

1. 実線（罫線）
- PDFのベクタ線そのもの（`page.lines`）を使って列・行境界を取る方法

2. 疑似線（座標クラスタ）
- OCR単語座標（`x`, `y`, `bbox`）から、ヘッダ位置・X中心・近傍距離で
  表境界を近似再構成する方法

---

## 2. モジュール別の境界信号マップ

| モジュール | 主用途 | 境界信号の主手段 | 補助/フォールバック |
|---|---|---|---|
| `extractors/vector_extractor.py` | M-Eの機器表（vector側） | 実線（罫線） | ヘッダ語ベースのセル抽出フォールバック |
| `extractors/raster_extractor.py` | M-Eの盤表（raster側） | 疑似線（ヘッダ語 + 座標クラスタ） | 旧左右分割ロジックへページ単位フォールバック |
| `extractors/e055_extractor.py` | E-055 | 疑似線（Xクラスタ `block_index`） | 低信頼時のみ線認識補助（vector線 + image線） |
| `extractors/e251_extractor.py` | E-251 | 疑似線（セクション帯 + Xクラスタ） | 器具記号アンカー補完 |

---

## 3. `vector_extractor`（実線中心）

対象: M-Eの機器表PDF

### 3.1 どう読んでいるか

1. `pick_target_tables`
- ページ上部の対象テーブル候補を選ぶ

2. `collect_grid_lines`
- `pdfplumber` の `page.lines` から縦線・横線を抽出
- 線長やbbox内存在率で有効線だけ残す

3. `extract_grid_rows`
- `explicit_vertical_lines / explicit_horizontal_lines` を指定して
  表セルを抽出（罫線を直接使う）

4. `extract_records`
- 機器番号を起点に継続行を1レコードへ連結

### 3.2 罫線が崩れたとき

`collect_grid_lines` / `extract_grid_rows` が失敗した場合は、  
`_extract_rows_via_table_cells` にフォールバックし、  
ヘッダ語（機器番号/名称/消費電力/台数）から列を再解決します。

つまり vector 側は「実線優先、語彙フォールバック」です。

---

## 4. `raster_extractor`（疑似線中心）

対象: M-Eの盤表PDF

### 4.1 どう読んでいるか

1. OCRで単語とbboxを取得（Vision API）
- `extract_words`

2. ヘッダ行アンカーを検出
- `detect_header_anchors`
- 文字列カテゴリ（code/name/voltage/power）を満たす行を表ヘッダ候補にする

3. 表候補bboxを作る
- `detect_table_candidates_from_page_words`

4. 列境界を推定
- `infer_column_bounds`
- ヘッダ語位置から4列境界（機器番号/名称/電圧/容量）を近似決定

5. 行クラスタ化して列へ割当
- `cluster_by_y` + `assign_column`

### 4.2 ポイント

Raster側は罫線を直接トレースしていません。  
OCR座標から列境界を推定するため、疑似線方式です。

---

## 5. `e055_extractor`（疑似線 + 低信頼時のみ線補助）

対象: E-055

### 5.1 通常経路（疑似線）

1. セクション候補行を抽出
2. `_cluster_x_positions` でX中心をクラスタ化
3. `block_index` を付与
4. `_propagate_equipment_in_section` で継続行を近傍制約付きで補完

### 5.2 低信頼時の線認識補助

`_should_run_line_assist` が低信頼と判定した場合のみ、  
次を実行します。

- `_collect_vector_vertical_lines`（pdfplumberの縦線）
- `_collect_image_vertical_lines`（OpenCVで画像縦線）
- `_apply_line_assist_if_confident`（高信頼かつ品質改善時のみ採用）

採用判定は保守的です。  
`confidence` を超えても、品質改善がない場合は `adopted=False` で既存結果を維持します。

---

## 6. `e251_extractor`（疑似線中心）

対象: E-251

### 6.1 どう読んでいるか

1. `_extract_section_words`
- 「住戸内 照明器具姿図」タイトルから対象帯を切り出す

2. `_extract_candidates_from_cluster`
- 行ごとに `器具記号:メーカー型番` / `メーカー:型番` / `メーカー 型番` を抽出

3. `_detect_anchors`
- 枠上部の `D1/L1` などの記号位置をアンカー化

4. `_assign_equipment_from_anchors`
- 記号欠落行へ最寄りアンカーを補完

5. `_assign_block_indexes`
- `row_x` クラスタから block を作り、左→右の読み順を固定

### 6.2 ポイント

E-251も罫線を直接読む主設計ではなく、  
セクション帯 + Xクラスタ + アンカー距離で枠を近似再構成しています。

---

## 7. なぜこのハイブリッド設計か

理由は次の3つです。

1. PDFタイプが混在するため
- vector PDF には罫線が残るが、raster/scan では罫線品質が不安定

2. 実運用で頑丈性が必要なため
- 疑似線は多少のレイアウト揺れやOCRノイズに強い
- 実線は取れる場面で高精度

3. 性能と安全性の両立のため
- E-055の線認識は常時ONではなく低信頼時だけ実行
- しかも品質改善時のみ採用する保守的合流

---

## 8. 制約（説明時に添えるべき注意）

1. 「全部が罫線認識」ではない
- モジュールごとに実線/疑似線の比重が違う

2. 疑似線は閾値依存
- Xクラスタ幅・Y距離・近傍判定の閾値調整が必要な図面がある

3. 線認識補助は万能ではない
- E-055では補助結果が品質改善しない場合、意図的に採用しない

---

## 9. 社内説明テンプレート（短文）

「本アプリは、表の境界信号を復元して読み取る方式です。  
境界信号は実線（罫線）と疑似線（座標クラスタ）の2系統で、  
機器表vectorは実線優先、raster/E-251/E-055は疑似線優先、  
E-055のみ低信頼時に線認識補助を併用します。」
