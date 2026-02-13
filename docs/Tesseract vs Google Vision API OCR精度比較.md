# Tesseract vs Google Vision API：PDF文字・表読み取り精度の比較

## 結論（概要）

全体として、Google Vision API は Tesseract に対して**文字認識精度で一貫して優位**である。特に複雑なフォント、手書き、低品質スキャン、日本語を含む多言語文書において差が顕著となる。一方、**レイアウト（段組・表構造）の検出**では Tesseract のほうが優れている場面がある。以下、実験データ・論文・口コミを交えて詳しく比較する。

***

## 文字認識精度の定量比較

### 学術論文・ベンチマークデータ

複数の学術研究が、Google Vision API と Tesseract の文字認識精度を定量的に比較している。

| 研究 / データセット | Tesseract 精度 | Google Vision API 精度 | 備考 |
|---|---|---|---|
| パンジャブ語新聞デジタル化 [^1] | 単語 97.20%、文字 92.48% | 単語 98.86%、文字 95.62% | Google APIの方がエラー率0%のセグメントが多数 |
| タイ車両登録証 [^2] | 47.02% | 84.43% | 画像前処理（シャープ化・明度調整）込み |
| 新聞テキスト認識（R&D研究）[^3] | Google Vision に劣る | 精度・感度・適合率すべて優位 | Confusion Matrixによる評価 |
| 手書き答案用紙（50枚テスト）[^4] | 全体 80–85%、手書き 20–40% | 全体 98%、手書き 80–95% | 手書き認識で特に大差 |
| 標本ラベル読取り [^5] | CER = 92%（非常に低精度） | 一貫して Tesseract を上回る | すべての印刷データセットで Google が優位 |
| スキャン財務報告書4エンジン比較 [^6] | ― | 平均エラー率 0.52%（ほぼ完璧） | Google Vision がトップスコア |

きれいな白黒印刷テキストの場合、Tesseract でも **95%以上**の文字認識精度に達することがあるが、画像品質が下がったり複雑なレイアウトになると精度が急激に低下する。Google Vision API は混合データセット（印刷・メディア・手書き）で **約98%**の精度を維持する。[^7]

### 精度差が生まれる主な理由

- **機械学習モデルの差**: Google Vision は Google の大規模データで学習した深層学習モデルを使用し、Tesseract は LSTM ベースだが学習データの規模が限定的[^8][^7]
- **前処理への依存度**: Tesseract は画像のノイズ、コントラスト、解像度に非常に敏感で、前処理なしでは精度が大幅に低下する。Google Vision は前処理なしでも安定した結果を出す[^9][^7]
- **手書き対応**: Google Vision は50言語以上の手書き認識をサポートし 80–95% の精度を出すが、Tesseract は手書きに対してほぼ機能しない（20–40%）[^4]

***

## 日本語 OCR における比較

### Qiita 実験：ふるさと納税案内スキャン

日本語文書に対する実際の比較実験が Qiita で報告されている。ふるさと納税の案内をスキャンし、Tesseract / 前処理済みTesseract / Google Vision API / Document AI の4方式で比較した結果：[^9]

- **Tesseract（前処理なし）**: 「ワンストップ特例」→「ワンストッツ提例」、「6団体以上」→「6国体以上」など**多数の誤認識が発生**。黒帯上の白文字「ご注意ください」は認識不可[^9]
- **Tesseract（OpenCVで前処理後）**: 一部改善されたが依然として「ワンストップ翌例」「貼外へ転居」「邪麺市役所」など**誤字が多く残存**。黒帯文字も認識不可[^9]
- **Google Vision API**: **すべての文字が正確に認識**された。黒帯上の「ご注意ください」も正しく認識[^9]
- **Document AI**: Vision API 同様に**誤字なく正しく認識**[^9]

### Zenn 実験：オープンソース OCR 比較

日本語対応オープンソース OCR の精度比較（Tesseract / PaddleOCR / EasyOCR）では、日本語精度は **PaddleOCR > EasyOCR > Tesseract** の順で、Tesseract が最も低い評価だった。著者は「残念ながら速度も精度もGoogle Cloud Visionが圧倒的によい」と結論づけている。[^10]

### 日本語の特有事情

日本語 OCR において Tesseract が苦手な点は追加の辞書導入が必要なこと、縦書きへの対応が弱いこと、漢字・ひらがな・カタカナ混在テキストへの対応力が限定的なことが指摘されている。Google Vision API は日本語をネイティブサポートしており、追加設定なしで高精度を実現する。[^11][^12]

***

## レイアウト・表読み取りの比較

### Tesseract のレイアウト検出が優れる場面

Google Vision の最大の弱点は**レイアウト検出（特に段組み）**である。Programming Historian の詳細な比較研究では以下が明らかになっている：[^13]

- Google Vision は**段組み（マルチカラム）を正しく認識できない**ことが多く、2段組みのテキストを横方向に読んでしまい、文が意味不明に結合される[^13]
- Tesseract は段組みの検出で Google Vision より**はるかに優れている**[^13]
- 19世紀の歴史文書を用いた実験で、段組みページでは Google Vision 単体の出力が「完全に誤った」テキストになるケースが確認された[^13]

このため、**Tesseract のレイアウト検出 + Google Vision の文字認識を組み合わせる**ハイブリッド手法が提案されており、どちらの単独使用よりも高い精度を出す結果が示されている。[^13]

### 表（テーブル）の構造認識

Tesseract は出力が基本的に**プレーンテキストまたは hOCR**であり、表の構造（行・列・セルの関係）を保持する機能は持たない。表のある PDF を処理する場合、後処理が必要となる。[^7]

Google Vision API（特に Document AI の Form Parser / Layout Parser）は**表構造の認識機能を持つ**。2025–2026年にリリースされた Gemini ベースの Layout Parser により、表認識品質が大幅に向上している。[^7]

エンタープライズレベルの表抽出精度については、以下の階層が報告されている：[^14]

- **Tesseract 単体での表構造認識**: 簡単な表で約 91.3% だが、複雑なテーブル（入れ子・結合セル）では **73.4%** まで低下する
- **クラウド Vision/VLM**: 97%以上の表構造認識を達成

***

## 開発者の口コミ・実体験

### Stack Overflow での評価

Stack Overflow のスレッドでは、両方を使用した開発者から対照的な意見が出ている：[^15]

> 「Google Vision は Tesseract よりはるかに速い。1年前であれば精度もはるかに上だった。Tesseract は最近 LSTM を導入し、最適化すれば2倍以上速くなるが、Google Vision には及ばない」

一方で逆の声もあり：

> 「最良・最悪の文書画像でテストしたところ、Google Vision は 66.6% の精度で、Tesseract は 82% だった。精度優先ならTesseract、速度優先ならGoogle Vision」[^15]

この相反する意見は、**文書の種類と前処理の有無**で大きく結果が変わることを示している。

### 総合的な口コミ傾向

多くの開発者レビューに共通する評価：[^16][^8][^11]

- **Tesseract のメリット**: 無料、オフライン利用可、多言語辞書の追加学習が可能、きれいな印刷テキストには十分な精度
- **Tesseract のデメリット**: ノイズや低解像度に弱い、手書き非対応、日本語は追加設定が必要、表やフォームの構造認識なし
- **Google Vision のメリット**: 高い文字認識精度、手書き対応、多言語ネイティブ対応、セットアップ後は使いやすい
- **Google Vision のデメリット**: レイアウト検出が弱い（特に段組み）、クラウド依存、コストが発生（$1.50/1000ページ）、Google サービスの長期安定性への懸念

ある開発者は「Tesseract は開発者向けのツール、Vision API と Document AI はビジネスユーザー向け」と総括している。[^8]

***

## コストパフォーマンス

| 項目 | Tesseract | Google Vision API |
|---|---|---|
| 料金 | **完全無料**（オープンソース）| 月1,000ページ無料、以降 **$1.50/1000ページ** [^13] |
| インフラ | ローカル実行（CPU）| Google Cloud Platform 必要 |
| セットアップ | 言語パッケージの追加インストールが必要 | GCPアカウント・サービスアカウントキーの設定が必要 |
| 処理速度 | CPUに依存、比較的遅い | クラウド処理で**高速**（1–2秒/ページ）[^4] |

日本語で800枚のスキャン画像を Google Vision API で処理した開発者の報告では、コストは**100円以下**だったとされている。[^9]

***

## 推奨される使い分け

| ユースケース | 推奨ツール | 理由 |
|---|---|---|
| きれいな印刷テキスト（英語） | Tesseract でも十分 | 95%以上の精度を出せる[^7] |
| 日本語文書 | **Google Vision API** | Tesseract は日本語で誤認識が多い[^9][^10] |
| 手書き文字を含む文書 | **Google Vision API** | 80–95% vs 20–40% の大差[^4] |
| 段組みのある文書 | **Tesseract + Google Vision の組合せ** | Tesseract のレイアウト検出 + Google Vision の文字認識[^13] |
| 表・フォーム構造の抽出 | **Google Document AI** または **Amazon Textract** | 構造認識機能が組み込まれている[^7] |
| 大量処理（コスト重視） | **Tesseract**（無料） | 精度が許容範囲なら費用ゼロ |
| 低品質スキャン・ノイズ画像 | **Google Vision API** | 前処理なしでも安定[^9][^16] |

***

## まとめ

文字認識の精度だけを見れば、Google Vision API は Tesseract を**多くの条件で上回る**（概ね 95–99% vs 80–95%）。ただし、段組みなどのレイアウト検出では Tesseract に優位性がある。日本語 PDF においては Google Vision API の優位性が特に顕著で、前処理なしでも高精度な結果が得られる。表の構造抽出については、どちらも単体では不十分だが、Google Document AI（Vision API の上位サービス）は表認識機能を備えている。最適な選択は文書の種類、言語、品質、予算によって異なり、両者を組み合わせたハイブリッドアプローチも有力な選択肢である。

---

## References

1. [Performance Comparison of Tesseract and Google ...](https://bpasjournals.com/library-science/index.php/journal/article/download/648/401/957) - Specifically,. Tesseract achieved an accuracy of 97.20% at the word level and 92.48% at the characte...

2. [Comparative analysis of Tesseract and Google Cloud ...](https://www.slideshare.net/slideshow/comparative-analysis-of-tesseract-and-google-cloud-vision-for-thai-vehicle-registration-certificate/252067973) - The study evaluates the performance of both optical character recognition (OCR) systems, revealing t...

3. [Comparative Analysis of Google Vision OCR with Tesseract ...](https://epublikasi.digitallinnovation.com/index.php/mcs/article/view/178) - NPT Prakisya 著 · 2024 · 被引用数: 10 — The results of this study conclude that Google Vision's Optical C...

4. [Best OCR for Answer Sheets: Google vs Tesseract vs ...](https://www.eklavvya.com/blog/best-ocr-answersheet-evaluation/) - Quick Verdict: For handwritten answer sheet evaluation, Google Cloud Vision achieves 80-95% accuracy...

5. [High‐throughput information extraction of printed specimen ...](https://besjournals.onlinelibrary.wiley.com/doi/10.1111/2041-210x.70235) - Relative to Tesseract OCR (CER = 92%, WER ... Across all printed data sets, the Google Cloud Vision ...

6. [Comparing Four OCR Engines for Scanned Financial ...](https://www.linkedin.com/pulse/comparing-four-ocr-engines-scanned-financial-reports-study-qomar) - Among the four OCR engines tested in this project, Google Cloud Vision OCR produced an almost perfec...

7. [Comparative Analysis of AI OCR Models for PDF to Structured Text](https://intuitionlabs.ai/articles/ai-ocr-models-pdf-structured-text-comparison) - Textract's OCR engine is powerful: it was benchmarked to have accuracy on par with Google's in many ...

8. [Vision API、Document AIのOCR比較：精度、価格、使い方の違い](https://tecnohakase.one/534/) - Vision APIは、Googleの強力な画像認識技術を活用しており、50言語以上をサポートしていますが、精度はTesseractよりも高いです。Document AIは、最も高精度のOCRツールで...

9. [Tesseract、Vision API、Document AIでのOCR比較 #Python - Qiita](https://qiita.com/satoru-karibe/items/b5b36bb341a82e2a5dc3) - OCRライブラリの精度が低い場合にはCloud Vision APIやDocument AIを用いることで、より高い認識精度を得ることができるかもしれません。

10. [日本語対応オープンソースOCRの比較 - Zenn](https://zenn.dev/piment/articles/254dde3ecf7f10) - 日本語対応のオープンソースの各種OCRの精度と時間を調べました。 ... でも、残念ながら速度も精度もGoogle Cloud Visionが圧倒的によいです。。。

11. [【備忘】OCRサービス・エンジンの比較 #文字認識 - Qiita](https://qiita.com/tks_sakigake/items/30e021ecfa6b5653e913) - Amazon Textract、Azure AI Vision、Google Cloud Vision API、Tesseract-ocrの出力を比較していた時の備忘録です。どれを採用するか判断に迷っ...

12. [ocr高精度化のコツ｜Pythonで実装する無料OCR比較と前処理技術](https://book.st-hakky.com/data-science/high-precision-text-extraction-using-ocr-in-python) - TesseractはGoogleが支援して開発を続ける歴史あるOCRエンジンで、100以上の言語に対応しています。文字認識の安定性に優れていますが、日本語の精度はやや限定的です。

13. [OCR with Google Vision API and Tesseract - Programming Historian](https://programminghistorian.org/en/lessons/ocr-with-google-vision-and-tesseract) - Layout detection accuracy: In comparison to Google Vision, Tesseract performs a lot better at layout...

14. [97.9% Table Extraction Accuracy: A 3-Tier OCR Cascade ...](https://adverant.ai/docs/research/table-extraction-ocr) - Our approach achieves 97.9% table structure recognition accuracy on the PubTables-1M benchmark while...

15. [Does Google Cloud Vision OCR API have better accuracy ...](https://stackoverflow.com/questions/45559285/does-google-cloud-vision-ocr-api-have-better-accuracy-and-performance-than-tesse) - I have used both of them. Google Vision is much faster than Tesseract and If it was a year back then...

16. [DeepSeek OCR: Why Performance Breaks Down on Real-World ...](https://featured.com/questions/deepseek-ocr-vs-tesseract-google-vision-tips) - Compared to Google Vision, DeepSeek feels closer in accuracy but faster to iterate with, especially ...

