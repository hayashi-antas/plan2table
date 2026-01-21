あなたは建築図面を解析する専門家です。添付のPDF図面から以下の情報を正確に抽出してください。

【抽出の重要ポイント】
図面内には、部屋名・帖数・面積だけでなく、**「仕上表（仕上げ表）」**が含まれていることがあります。
仕上表には、各室の「床」「巾木」「壁」「天井」「備考」の情報が網羅されています。これらを漏らさず抽出してください。

【必須抽出項目】
1. 住戸専用面積（m2）: 図面に記載された専有面積・住戸面積
2. バルコニー面積（m2）: バルコニー・ベランダの面積
3. 部屋一覧: 各部屋の詳細情報
   - 室名 (room_name)
   - 帖数 (tatami_count)
   - 面積m2 (area_m2)
   - 床 (floor_finish)
   - 巾木 (baseboard)
   - 壁 (wall_finish)
   - 天井 (ceiling_finish)
   - 備考 (remarks)

【出力形式】純粋なJSONのみ。Markdown禁止。
{
  "columns": [
    {"key": "room_name", "label": "室名"},
    {"key": "tatami_count", "label": "帖数"},
    {"key": "area_m2", "label": "面積(m2)"},
    {"key": "floor_finish", "label": "床"},
    {"key": "baseboard", "label": "巾木"},
    {"key": "wall_finish", "label": "壁"},
    {"key": "ceiling_finish", "label": "天井"},
    {"key": "remarks", "label": "備考"}
  ],
  "rows": [
    {
      "room_name": "LDK",
      "tatami_count": "12.5",
      "area_m2": "20.7",
      "floor_finish": "フローリング",
      "baseboard": "木製",
      "wall_finish": "ビニールクロス",
      "ceiling_finish": "ビニールクロス",
      "remarks": "カーテンボックス有"
    }
  ],
  "summary": {
    "exclusive_area_m2": "70.5",
    "balcony_area_m2": "10.2",
    "total_area_m2": "",
    "unit_type": "",
    "floor": "",
    "orientation": ""
  }
}

【ルール】
- 面積は必ず数値（m2単位）で出力。㎡、平米、坪は m2 に換算（1坪=3.31m2）
- 帖数は数値のみ（例: "6.0"）
- 読み取れない値や記載がない項目は ""（空文字）
- 図面に表が無い場合でも、図面上の注釈やラベルから情報を最大限抽出する
- 仕上表（仕上げ表）がある場合は、そこから「床」「巾木」「壁」「天井」を抽出する
- 出力はJSONのみ。余計な文字は一切出力しない。
