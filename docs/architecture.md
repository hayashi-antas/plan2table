# FastAPI + HTMX アーキテクチャ

本アプリケーションは **FastAPI**（バックエンド）と **HTMX**（フロントの軽量ライブラリ）を組み合わせた構成です。サーバーが HTML を返し、HTMX がその HTML で画面の一部を差し替える「HTML over the wire」スタイルです。

## 1. 全体像

```
[ブラウザ]  ←→  [FastAPI サーバー]
    ↑                    ↑
  HTMX                  Python
  (HTMLの一部を          (ルート・ビジネスロジック・
   差し替え)              HTML文字列を返す)
```

- **FastAPI**: バックエンド。リクエストを受け、処理して **HTML の断片（またはページ全体）** を返す。
- **HTMX**: フロントの小さな JS ライブラリ。フォーム送信などを **AJAX で送り、返ってきた HTML で画面の一部だけを差し替える**。

API が JSON を返すのではなく、**サーバーが HTML を返す**設計です。

## 2. FastAPI 側の構造

### アプリの組み立て（`app/main.py`）

- **FastAPI()** でアプリを作成。
- **`app.mount("/static", ...)`** で CSS/JS などの静的ファイルを配信。
- **`app.include_router(...)`** でルートをモジュールごとに追加。

### ディレクトリと役割

```
app/
├── routers/
│   ├── pages.py     … 初期表示用の「ページ全体」を返す（GET /area, /me-check など）
│   ├── area.py      … 図面解析の POST（/area/upload）
│   ├── extractors.py … E-055/E-251/E-142 の POST
│   ├── mecheck.py   … /customer/run, /raster/upload など
│   └── downloads.py … CSV ダウンロード（GET）
├── core/            … 設定・レンダラ（HTML 組み立て）
└── services/       … ビジネスロジック（抽出ジョブ、Gemini など）
```

- **GET**: 画面を開いたとき用。Jinja2 で `templates/xxx.html` をレンダして **ページ全体の HTML** を返す（`pages.py`）。
- **POST**: フォーム送信・ファイルアップロード用。処理後に **HTML の断片**（成功メッセージやテーブルなど）を文字列で返す（`area.py`, `extractors.py`, `mecheck.py`）。返す型は `response_class=HTMLResponse` で「HTML です」と宣言しているだけです。

## 3. HTMX の役割

HTMX は **HTML に属性（`hx-*`）を書くだけで**、「この要素がクリック/送信されたら AJAX でこの URL に送って、返ってきた HTML をこの要素に入れる」を実現します。

### 典型的なパターン（例: `templates/me-check.html`）

```html
<form hx-encoding="multipart/form-data"
      hx-post="/customer/run"
      hx-target="#customer-result"
      hx-indicator="#customer-loading">
  <!-- ファイル入力など -->
</form>
<div id="customer-loading" class="htmx-indicator">...</div>
<div id="customer-result"></div>
```

| 属性 | 意味 |
|------|------|
| `hx-post="/customer/run"` | フォーム送信を **POST /customer/run** に送る（ページ遷移しない） |
| `hx-target="#customer-result"` | サーバーから返ってきた **HTML を `#customer-result` の中に挿入**する |
| `hx-indicator="#customer-loading"` | リクエスト中は `#customer-loading` を表示する（HTMX が class で制御） |

### リクエストの流れ

1. ユーザーがフォームを送信する。
2. HTMX が `/customer/run` に POST（multipart）を送る。
3. FastAPI の `@router.post("/customer/run", response_class=HTMLResponse)` が動く（`mecheck.py` の `handle_customer_run`）。
4. サーバーが **HTML の文字列**（成功ならテーブル、失敗ならエラー用 HTML）を返す。
5. HTMX がその HTML で `#customer-result` の中身を置き換える。

**JavaScript で fetch や DOM 操作をほとんど書かずに**、「送信 → 返ってきた HTML で一部更新」ができます。

## 4. この構成の特徴

- **サーバーが HTML を返す**: SPA のように「JSON API + フロントで DOM 構築」ではなく、**サーバーで HTML を組み立てて返す**。
- **HTMX が「差し替え」だけ担当**: どの URL に送るか・返ってきた HTML をどこに置くかは属性で指定。ロジックはサーバーに集約される。
- **FastAPI のルート = エンドポイント**: 画面表示用の GET（`pages`）と、フォーム送信用の POST（`area`, `extractors`, `mecheck`）がそれぞれ「何の URL で、何を返すか」を決めている。

まとめると、**FastAPI がルーティング・処理・HTML 生成を担当し、HTMX がその HTML をブラウザの決まった場所に差し込む**アーキテクチャです。
