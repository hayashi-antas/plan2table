# E-251 抽出機能 詳細設計（開発者向け）

このドキュメントは `E-251` の抽出ロジックを、**なぜこの実装にしたか**を含めて説明する開発者向け資料です。  
対象実装: `extractors/e251_extractor.py`

---

## 1. 目的と前提

`E-251` 図面の「住戸内 照明器具姿図」から、以下3列を安定抽出します。

1. `器具記号`
2. `メーカー`
3. `相当型番`

現場要件として、以下を満たす必要があります。

- 記号は `D1`, `D2`, `L1(L1500)` などを保持する
- 記号でないマーク（丸H等）は `器具記号=""` にする
- 1行内の複数表記（`D1... D2... L5... Panasonic...` など）を取りこぼさない
- CSV順序は人の読み順（左ブロック→右ブロック、各ブロック内は上→下）

---

## 2. 全体フロー

`extract_e251_pdf()` がエントリポイントです。

1. Visionクライアント作成（`build_vision_client`）
2. PDFページ解決（`resolve_target_pages`）
3. 各ページで `pdftoppm` により画像化
4. `document_text_detection` で単語＋bbox取得（`_extract_words`）
5. 「住戸内 照明器具姿図」セクションへ空間的に絞り込み（`_extract_section_words`）
6. 行クラスタごとに候補抽出（`_extract_candidates_from_cluster`）
7. 器具記号アンカーで補完（`_assign_equipment_from_anchors`）
8. Xクラスタから block_index 付与（`_assign_block_indexes`）
9. 読み順にソートして最終行化（`build_output_rows`）
10. BOM付きCSV保存（`write_csv`）

---

## 3. 重要設計と理由

### 3.1 セクション切り出しを先に行う理由

`_extract_section_words()` で図面全体OCRから「住戸内 照明器具姿図」周辺だけを切り出します。

- タイトル行検出: `住戸内` と `照明器具姿図` を同時に含む行
- 切り出し範囲: タイトル行を起点に `x_min` 以右、`y_min..y_max` の帯領域

理由:

- E-251 は同一ページに別表・注記・系統図が多数あり、全域解析だと誤検出が増える
- セクションを先に絞る方が、後段の正規表現とヒューリスティックを単純化できる

### 3.2 3種類の表記を同時抽出する理由

`_extract_candidates_from_cluster()` は以下パターンを扱います。

1. `器具記号(...) : メーカー 型番`
2. `メーカー:型番`
3. `メーカー 型番`

主な正規表現:

- `EQ_COLON_MAKER_MODEL_PATTERN`
- `MAKER_COLON_MODEL_PATTERN`
- `MAKER_SPACE_MODEL_PATTERN`

理由:

- 図面内で記法が混在する（L系は `Lx(...) :`、D2は `DNL:D-EX12`、D1/Panasonicは `maker model`）
- どれか1種類に寄せると取りこぼしが出る

補足:

- `MODEL_TOKEN_PATTERN` は、`DSY - 4394...` のような**空白付きハイフン**も許容
- `occupied_spans` で、同じ文字領域の重複抽出を防止

### 3.3 器具記号アンカー補完を使う理由

`_detect_anchors()` はタイトル直下帯（`title_y..title_y+120`）から、枠上部の記号を抽出します。

- `D` + `1` の分割OCRは結合して `D1` 扱い
- `D1`, `D2`, `L1` などは有効コード
- 単独英字（例 `H`）はシンボルとして扱い、`equipment=""`

`_assign_equipment_from_anchors()` で、候補行に器具記号がない場合は最寄りアンカーを割当。

理由:

- `メーカー 型番` 行には器具記号が同一行に存在しないケースがある
- 枠構造のX位置を使うのが最も安定する
- 距離しきい値 (`max_distance=520`) で、遠方誤マッチを抑制

### 3.4 読み順ソートを block ベースにした理由

`_assign_block_indexes()` で `row_x` をクラスタ化し、`block_index` を付与します。
`build_output_rows()` は以下順でソートします。

- `page`
- `block_index`（左→右）
- `row_y`（上→下）
- `row_x`

理由:

- 単純な `row_y` 優先だと、右枠の上段が左枠下段より先に出ることがある
- 実運用で求められる「人が読む順」は列（枠）優先
- `E-055` の順序思想と合わせて運用一貫性を持たせる

---

## 4. 正規化・フィルタリング仕様

### 4.1 記号正規化

`_normalize_equipment_label()`:

- `NFKC` + ダッシュ正規化 + 空白除去
- `L1 ( L1500 )` のようなOCR揺れを `L1(L1500)` へ統一
- `EQUIPMENT_LABEL_PATTERN` に一致しないものは空文字

### 4.2 型番正規化

`_cleanup_model()`:

- ダッシュ種別統一
- `D - EX12` → `D-EX12` に寄せる
- 余分記号や多重空白を除去

### 4.3 ノイズ除外

`_is_likely_model()` で最低限のノイズを弾きます。

- 数字を含まない語を除外
- `LED0.5W` のようなワット数のみ表現を除外
- `PF*`, `VVF*`, `SCV*` 等の配線系トークンを除外

注記行は `型番は相当品とする` / `注記` 判定でスキップ。

---

## 5. 柔軟性（どこまで吸収できるか）

現実に吸収できる揺れ:

- 全角/半角混在（NFKC）
- ダッシュ種別の揺れ（`-`, `−`, `–` など）
- ハイフン前後の空白（`DSY - 4394...`）
- 記号と長さの空白揺れ（`L1 (L1500)`）
- 同一行複数アイテム（L系+Panasonic等）の同時抽出

調整ポイント:

- 行クラスタ閾値: `y_cluster`（既定 `14.0`）
- ブロックXクラスタ閾値: `_cluster_x_positions(... tolerance=260.0)`
- アンカー適用距離: `max_distance=520.0`

---

## 6. 制約と既知リスク

1. レイアウト依存
- タイトル起点で帯領域を切るため、極端なレイアウト変更には弱い

2. 英字メーカー依存
- メーカーは英字始まりを前提にした判定を含む
- 将来、和文メーカー表記が主になると見直しが必要

3. 記号形式の制約
- 記号は `^[A-Z]\d{1,2}(\([^()]+\))?$` 前提
- 例: `DL10A` のような拡張形式には未対応

4. `debug_dir` の未活用
- 引数は受けるが、現時点で E-055 のような詳細診断JSONは未実装

---

## 7. 拡張時ガイド

### 7.1 記号体系を増やす場合

- `EQUIPMENT_LABEL_PATTERN` を拡張
- `tests/test_e251_extractor.py` にケースを追加

### 7.2 誤検出が増えた場合

優先順:

1. `_is_likely_model()` の除外条件を強化
2. アンカー距離 `max_distance` を縮小
3. セクション切り出しの `y_max` / `title_y+120` 調整

### 7.3 並び順要件が変わる場合

- `build_output_rows()` のソートキーを変更
- 変更時は「左→右/上→下」の期待順テストを必ず更新

---

## 8. テスト戦略

主テスト:

- `tests/test_e251_extractor.py`
  - 3形式抽出
  - 記号保持/空欄化
  - `L1(L1500)` 形式保持
  - 読み順（block優先）

- `tests/test_integration_routes.py`
  - `/e-251/upload` の成功/失敗
  - `/jobs/{job_id}/e251.csv` の固定パス検証

- `tests/test_job_store.py`
  - `kind="e251"` -> `e251.csv`

推奨運用:

- ロジック変更時は `python3 -m pytest -q` をフルで実行
- 実データ確認は `E-251.pdf` で `/tmp/e251_real.csv` を目視確認

---

## 9. 現時点の期待出力（基準）

基準PDF: `<PDF_PATH>`

期待9行:

1. `D1,DAIKO,LZD-93195XW`
2. `D2,DNL,D-EX12`
3. `L1(L1500),DAIKO,DSY-4394YWG`
4. `L2(L1200),DAIKO,DSY-4393YWG`
5. `L3(L900),DAIKO,DSY-4392YWG`
6. `L4(L600),DAIKO,DSY-4391YWG`
7. `L5(L300),DAIKO,DSY-4390YWG`
8. `,Panasonic,WTF4088CWK`
9. `,Panasonic,WTL40944W`

この出力を「仕様の生きた基準」とし、変更時の回帰判定に使います。
