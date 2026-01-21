import os
import json
import html
from google import genai
from google.genai import types
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
import markdown
from prompts import load_prompt
from extractors.text_extractor import extract_text_from_pdf
from extractors.area_regex import extract_summary_areas
from extractors.tool_definitions import TOOLS, SKILL_REGISTRY

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize Vertex AI (new google-genai client)
# We use the environment variable GOOGLE_CLOUD_PROJECT as requested.
project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
location = os.getenv("VERTEX_LOCATION", "global")
MODEL_NAME = os.getenv("VERTEX_MODEL_NAME", "gemini-3-flash-preview")
genai_client = None

# Handle Credentials for Hugging Face Spaces
# If GCP_SERVICE_ACCOUNT_KEY env var exists (JSON content), write it to a file
service_account_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
if service_account_json:
    cred_file_path = "gcp_credentials.json"
    with open(cred_file_path, "w") as f:
        f.write(service_account_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file_path
    print(f"Credentials saved to {cred_file_path}")

try:
    if project_id:
        genai_client = genai.Client(vertexai=True, project=project_id, location=location)
    else:
        genai_client = genai.Client(vertexai=True, location=location)
except Exception as exc:
    print(f"Failed to initialize Vertex AI client: {exc}")

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

def _build_summary_html(summary):
    if not isinstance(summary, dict) or not summary:
        return ""
    labels = {
        "exclusive_area_m2": "住戸専用面積(m2)",
        "balcony_area_m2": "バルコニー面積(m2)",
        "total_area_m2": "延床面積(m2)",
        "unit_type": "間取りタイプ",
        "floor": "階数",
        "orientation": "方位",
    }
    rows = []
    for key, label in labels.items():
        value = _stringify_cell(summary.get(key, ""))
        if value == "":
            continue
        rows.append((label, value))
    if not rows:
        return ""

    card_class = "mb-6 rounded-xl border border-slate-700 bg-slate-800/70 p-4 text-slate-200"
    title_class = "mb-3 text-sm font-semibold uppercase tracking-wider text-sky-300"
    grid_class = "grid grid-cols-1 gap-3 md:grid-cols-2"
    label_class = "text-xs uppercase tracking-wider text-slate-400"
    value_class = "text-sm text-slate-100"
    items = []
    for label, value in rows:
        items.append(
            f"<div><div class=\"{label_class}\">{html.escape(label)}</div>"
            f"<div class=\"{value_class}\">{html.escape(str(value))}</div></div>"
        )
    return (
        f"<section class=\"{card_class}\">"
        f"<div class=\"{title_class}\">住戸概要</div>"
        f"<div class=\"{grid_class}\">{''.join(items)}</div>"
        f"</section>"
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

def _build_debug_html(extracted_text, regex_summary, raw_text):
    text_snippet = (extracted_text or "").strip() or "(no text extracted)"
    if len(text_snippet) > 2000:
        text_snippet = text_snippet[:2000] + "\n... (truncated)"
    text_snippet = html.escape(text_snippet, quote=True)

    regex_json = html.escape(json.dumps(regex_summary, ensure_ascii=False, indent=2), quote=True)
    raw_snippet = (raw_text or "").strip() or "(empty response)"
    if len(raw_snippet) > 2000:
        raw_snippet = raw_snippet[:2000] + "\n... (truncated)"
    raw_snippet = html.escape(raw_snippet, quote=True)

    container_class = "mt-6 rounded-xl border border-slate-700 bg-slate-900/60 p-4 text-slate-200"
    summary_class = "cursor-pointer text-sm font-semibold text-sky-300"
    pre_class = "mt-3 max-h-64 overflow-auto rounded-md bg-slate-900/80 p-3 text-xs text-slate-200"
    return (
        f"<details class=\"{container_class}\">"
        f"<summary class=\"{summary_class}\">デバッグ情報</summary>"
        f"<div class=\"mt-4 text-xs uppercase tracking-wider text-slate-400\">抽出テキスト</div>"
        f"<pre class=\"{pre_class}\">{text_snippet}</pre>"
        f"<div class=\"mt-4 text-xs uppercase tracking-wider text-slate-400\">正規表現ヒット</div>"
        f"<pre class=\"{pre_class}\">{regex_json}</pre>"
        f"<div class=\"mt-4 text-xs uppercase tracking-wider text-slate-400\">LLM生レスポンス</div>"
        f"<pre class=\"{pre_class}\">{raw_snippet}</pre>"
        f"</details>"
    )


def _get_function_calls(response):
    """Extract all function calls from all parts of all candidates."""
    func_calls = []
    try:
        candidates = response.candidates or []
    except Exception:
        return []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            func_call = getattr(part, "function_call", None)
            if func_call:
                func_calls.append(func_call)
    return func_calls


def _execute_function_call(func_call):
    name = getattr(func_call, "name", "")
    args = getattr(func_call, "args", None) or {}
    handler = SKILL_REGISTRY.get(name)
    if not handler:
        return name, {"error": f"Unknown tool: {name}"}
    try:
        result = handler(**args)
        return name, {"result": result}
    except Exception as exc:
        return name, {"error": str(exc)}


def _generate_with_tools(client, model_name, parts, generation_config):
    """Handle chat + tool execution loop with the google-genai models API."""
    if client is None:
        raise RuntimeError("Vertex AI client is not initialized.")

    config = types.GenerateContentConfig(
        tools=TOOLS,
        **generation_config,
    )

    messages = [types.Content(role="user", parts=parts)]
    response = client.models.generate_content(
        model=model_name,
        contents=messages,
        config=config,
    )

    for _ in range(6):
        func_calls = _get_function_calls(response)
        if not func_calls:
            break

        tool_responses = []
        for func_call in func_calls:
            name, payload = _execute_function_call(func_call)
            tool_part = types.Part.from_function_response(name=name, response=payload)
            tool_responses.append(tool_part)

        # Append model turn and tool responses to the conversation history
        messages.append(response.candidates[0].content)
        messages.append(types.Content(role="user", parts=tool_responses))

        response = client.models.generate_content(
            model=model_name,
            contents=messages,
            config=config,
        )
    return response

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
        
        # Extract text from PDF for rule-based fallback
        extracted_text = extract_text_from_pdf(file_bytes)
        regex_summary = extract_summary_areas(extracted_text)

        # Prepare the request for Vertex AI
        pdf_part = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        
        prompt_text = load_prompt("area_extract")
        prompt_part = types.Part.from_text(text=prompt_text)

        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 8192,
        }

        response = _generate_with_tools(
            genai_client,
            MODEL_NAME,
            [pdf_part, prompt_part],
            generation_config=generation_config,
        )
        
        raw_text = response.text or ""
        data = _extract_json(raw_text)
        if not isinstance(data, dict):
            return _render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")

        report_md = data.get("report_markdown", "")
        if not report_md:
            return _render_parse_error(raw_text, "レポート内容（report_markdown）が空です。")

        # Convert Markdown to HTML
        report_html = markdown.markdown(
            report_md,
            extensions=['tables', 'fenced_code']
        )

        # Build debug info
        debug_html = _build_debug_html(extracted_text, regex_summary, raw_text)
        
        # Wrapping in a styled div for better look
        styled_report = f"""
        <div class="prose prose-invert max-w-5xl mx-auto bg-slate-800/50 p-6 rounded-2xl border border-slate-700 shadow-xl space-y-6">
            {report_html}
        </div>
        """
        
        return styled_report + debug_html

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
