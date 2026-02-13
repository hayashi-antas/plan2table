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

建築図面・機器表・盤表などのPDFを「使えるデータ」に変えるWebアプリケーションです。

## Plan2Table

建築図面（PDF）から部屋情報を自動抽出し、Markdownレポートを生成します。  
寸法線がある詳細図面と面積が記載されている簡易図面の両方に対応し、AIエージェント（Function Calling）で高精度な抽出・検証を行います。

📄 **[Plan2Table 詳細ドキュメント](docs/plan2table.md)**

## M-E-Check

機器表PDFと盤表PDFをアップロードすると、両方の記載が一致しているかを自動照合します。  
機器番号・台数・容量(kW)の一致/不一致を一覧表で確認でき、結果はCSVでダウンロードできます。

📄 **[M-E-Check 詳細ドキュメント](docs/m-e-check.md)**


