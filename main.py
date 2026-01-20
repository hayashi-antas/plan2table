import os
import json
import html
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize Vertex AI
# We use the environment variable GOOGLE_CLOUD_PROJECT as requested.
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = "us-central1" # Defaulting to us-central1

# Handle Credentials for Hugging Face Spaces
# If GCP_SERVICE_ACCOUNT_KEY env var exists (JSON content), write it to a file
service_account_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
if service_account_json:
    cred_file_path = "gcp_credentials.json"
    with open(cred_file_path, "w") as f:
        f.write(service_account_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file_path
    print(f"Credentials saved to {cred_file_path}")

if project_id:
    vertexai.init(project=project_id, location=location)

def _stringify_cell(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)

def _extract_json(raw_text):
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = cleaned[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

def _normalize_columns(columns, rows):
    normalized = []
    seen = set()
    for col in columns:
        if not isinstance(col, dict):
            continue
        key = col.get("key") or ""
        label = col.get("label") or key
        if not key:
            key = f"col_{len(normalized) + 1}"
        key = str(key)
        if key in seen:
            key = f"{key}_{len(normalized) + 1}"
        normalized.append({
            "key": key,
            "label": str(label) if label is not None else key,
            "hint": str(col.get("hint") or "")
        })
        seen.add(key)
    if not normalized:
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in row.keys():
                key = str(key)
                if key in seen:
                    continue
                normalized.append({"key": key, "label": key, "hint": ""})
                seen.add(key)
    return normalized

def _build_table_html(columns, rows):
    safe_columns = _normalize_columns(columns, rows)
    safe_rows = [row for row in rows if isinstance(row, dict)]
    if not safe_columns:
        safe_columns = [{"key": "_empty", "label": "データなし", "hint": ""}]

    table_class = "min-w-full table-auto border border-slate-700 rounded-xl overflow-hidden bg-slate-800 text-slate-200"
    thead_class = "bg-slate-900"
    th_class = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-sky-300 border-b border-slate-700"
    td_class = "px-4 py-3 text-sm text-slate-200 border-b border-slate-700 whitespace-pre-wrap"
    row_class = "hover:bg-slate-700/60"
    empty_td_class = "px-4 py-6 text-sm text-slate-400 text-center"

    header_cells = []
    for col in safe_columns:
        label = html.escape(col["label"], quote=True)
        header_cells.append(f"<th class=\"{th_class}\">{label}</th>")

    body_rows = []
    if not safe_rows:
        colspan = len(safe_columns)
        body_rows.append(
            f"<tr class=\"{row_class}\"><td colspan=\"{colspan}\" class=\"{empty_td_class}\">データがありません</td></tr>"
        )
    else:
        for row in safe_rows:
            cells = []
            for col in safe_columns:
                key = col["key"]
                value = _stringify_cell(row.get(key, ""))
                cell_text = html.escape(value, quote=True)
                cells.append(f"<td class=\"{td_class}\">{cell_text}</td>")
            body_rows.append(f"<tr class=\"{row_class}\">" + "".join(cells) + "</tr>")

    return (
        f"<table class=\"{table_class}\">"
        f"<thead class=\"{thead_class}\"><tr>{''.join(header_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
    )

def _render_parse_error(raw_text, reason):
    snippet = (raw_text or "").strip() or "(empty response)"
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "\n... (truncated)"
    snippet = html.escape(snippet, quote=True)
    reason = html.escape(reason, quote=True)
    return f"""
    <div class="p-4 bg-amber-900/40 border border-amber-500 text-amber-100 rounded-lg">
        <strong>JSON解析に失敗しました:</strong> {reason}
        <pre class="mt-3 max-h-72 overflow-auto rounded-md bg-slate-900/60 p-3 text-xs text-slate-200">{snippet}</pre>
    </div>
    """

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload", response_class=HTMLResponse)
async def handle_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        return """
        <div class="p-4 bg-red-900/50 border border-red-500 text-red-200 rounded-lg">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """
    
    try:
        # Read file content
        file_bytes = await file.read()
        
        # Prepare the request for Vertex AI
        pdf_part = Part.from_data(data=file_bytes, mime_type="application/pdf")
        
        prompt_text = """あなたは建築図面を解析する専門家です。添付のPDF図面を解析し、表形式の情報を抽出してください。
出力は必ずJSONのみ。Markdown、コードブロック、前置き、説明文は禁止です。
必ず以下のトップレベル構造にしてください。
{
  "columns": [
    {"key": "room_name", "label": "室名", "hint": "任意"}
  ],
  "rows": [
    {"room_name": "玄関"}
  ],
  "meta": {"notes": "..."}
}
ルール:
- columnsは表示順序。labelは画面表示名。keyはrowsのキー名（英数字と_のみ推奨）。
- rowsの各行はcolumns.keyに対応する値を持つ。無い場合は""で良い。
- 図面に表が存在する場合：表の見出しをcolumns.labelにして忠実にcolumns/rows化する。
- 表が無い場合：推定でcolumnsを構成して良い（例：室名/面積/仕上げ/天井高 など）。
- 不明な値は""（空文字）。
- 出力はJSONのみ。余計な文字は一切出力しない。"""

        # Using gemini-2.0-flash for extraction
        model = GenerativeModel("gemini-2.0-flash")
        
        # Generate content
        response = model.generate_content(
            [pdf_part, prompt_text],
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 2048,
            }
        )
        
        raw_text = response.text or ""
        data = _extract_json(raw_text)
        if not isinstance(data, dict):
            return _render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")

        columns = data.get("columns", [])
        rows = data.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list):
            return _render_parse_error(raw_text, "columnsまたはrowsの形式が不正です。")

        return _build_table_html(columns, rows)

    except Exception as e:
        # Log the error for debugging (on the server console)
        print(f"Error processing upload: {e}")
        safe_error = html.escape(str(e), quote=True)
        return f"""
        <div class="p-4 bg-red-900/50 border border-red-500 text-red-200 rounded-lg">
            <strong>Error Processing Request:</strong><br>
            {safe_error}
        </div>
        """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
