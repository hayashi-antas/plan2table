---
title: Plan2table
emoji: 🌖
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

 <br>
 
## 建築図面・機器表PDFのチェックとデータ化を、一つのツールで行えます。

## Plan2Table

建築図面（PDF）から部屋情報を自動抽出し、Markdownレポートを生成します。  
寸法線がある詳細図面と面積が記載されている簡易図面の両方に対応し、AIエージェント（Function Calling）で高精度な抽出・検証を行います。

📄 **[Plan2Table 詳細ドキュメント](docs/plan2table.md)**
  
📄 **[How To Parse（境界信号ベース）](docs/how-to-parse.md)**

## M-E-Check

機器表PDFと盤表PDFをアップロードすると、両方の記載が一致しているかを自動照合します。  
機器番号・台数・容量(kW)の一致/不一致を一覧表で確認でき、結果はCSVでダウンロードできます。

📄 **[M-E-Check 詳細ドキュメント](docs/m-e-check.md)**
  
📄 **[M-E-Check FAQ（実装準拠）](docs/m-e-check-faq.md)**

## E-055 OCR

照明器具姿図PDF（E-055 など）をアップロードすると、器具記号と相当型番を抽出し、  
`機器器具 / メーカー / 型番` の3列で表示・CSV出力します。

📄 **[E-055 OCR 詳細ドキュメント](docs/e-055.md)**
  
📄 **[E-055 OCR FAQ](docs/e-055-faq.md)**

## E-251 OCR

住戸内照明器具姿図PDF（E-251 など）をアップロードすると、器具記号・メーカー・相当型番を抽出し、  
`器具記号 / メーカー / 相当型番` の3列で表示・CSV出力します。

📄 **[E-251 OCR 詳細ドキュメント](docs/e-251.md)**
  
📄 **[E-251 OCR FAQ（実装準拠）](docs/e-251-faq.md)**

## E-142 OCR

姿図PDF（E-142 など）をアップロードすると、大枠ごとに  
`タイトル, 番号, 表ラベル, 値, ...` の1行フラット形式でCSV出力します（ヘッダーなし）。

📄 **[E-142 OCR 詳細ドキュメント](docs/e-142.md)**

📄 **[E-142 OCR 詳細設計（開発者向け）](docs/e142-detail.md)**

📄 **[E-142 OCR FAQ（実装準拠）](docs/e142-faq.md)**
