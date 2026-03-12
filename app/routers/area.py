"""POST routes for area (diagram) extraction upload."""

import html

import bleach
import markdown
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse
from google.genai import types

from app.core.config import MODEL_NAME, genai_client
from app.core.renderers import build_debug_script, render_parse_error
from app.services.area_validation import get_diagram_type, validate_room_areas
from app.services.extraction_jobs import is_pdf_upload
from app.services.gemini import extract_json, generate_with_tools, get_response_text
from extractors.area_regex import extract_summary_areas
from extractors.text_extractor import extract_text_from_pdf
from prompts import load_prompt

router = APIRouter()


@router.post("/area/upload", response_class=HTMLResponse)
async def handle_area_upload(file: UploadFile = File(...)):
    if not is_pdf_upload(file):
        return """
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error:</strong> Please upload a valid PDF file.
        </div>
        """

    try:
        file_bytes = await file.read()

        extracted_text = extract_text_from_pdf(file_bytes)
        regex_summary = extract_summary_areas(extracted_text)

        pdf_part = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")
        prompt_text = load_prompt("area_extract")

        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 16384,
        }

        def _run_generation(extra_instruction=""):
            combined_prompt = prompt_text
            if extra_instruction:
                combined_prompt = f"{prompt_text}\n\n{extra_instruction}"
            combined_prompt_part = types.Part.from_text(text=combined_prompt)
            response, tool_calls_log = generate_with_tools(
                genai_client,
                MODEL_NAME,
                [pdf_part, combined_prompt_part],
                generation_config=generation_config,
            )
            raw_text = get_response_text(response) or ""
            data = extract_json(raw_text)
            return response, tool_calls_log, raw_text, data

        _response, tool_calls_log, raw_text, data = _run_generation()
        if not isinstance(data, dict):
            return render_parse_error(raw_text, "JSONオブジェクトが見つかりません。")

        diagram_type = get_diagram_type(data)
        print(f"[Debug] 図面タイプ: {diagram_type}")

        warnings, found_rooms, warning_rooms = validate_room_areas(data)

        if warnings:
            print(f"[Debug] 検出された室: {found_rooms}")
            print(f"[Debug] 警告: {warnings}")

            if diagram_type == "detailed":
                extra_instruction = (
                    "【追加指示 - 必ず実行してください】\n"
                    "この図面は寸法線がある「detailed」タイプです。\n"
                    "以下の部屋について、area_m2が空欄（「-」含む）になっています。\n"
                    "これらの部屋は仕上表に記載されているため、図面から寸法線を読み取って面積を計算する必要があります。\n\n"
                    f"**面積が未計算の部屋**: {', '.join(warning_rooms)}\n\n"
                    "【対応手順】\n"
                    "1. 各部屋の寸法線を図面から読み取る（幅mm × 奥行mm）\n"
                    "2. calculate_room_area_from_dimensions ツールを使用して面積を計算\n"
                    "3. room_areasのarea_m2フィールドに計算結果を記載\n"
                    "4. calculationフィールドに計算根拠（例: 「幅1000mm × 奥行1500mm = 1.50㎡」）を記載\n\n"
                    "推測や一般値は禁止。寸法(mm)を明示してください。\n"
                    "「-」や空欄にせず、必ず数値で埋めてください。"
                )
                _response, tool_calls_log, raw_text, data = _run_generation(
                    extra_instruction
                )
                if not isinstance(data, dict):
                    return render_parse_error(
                        raw_text, "JSONオブジェクトが見つかりません。"
                    )

                warnings, found_rooms, warning_rooms = validate_room_areas(data)

            if warnings:
                print(f"[Warning] 以下の警告がありますが、処理を続行します: {warnings}")

        report_md = data.get("report_markdown", "")
        if not report_md:
            return render_parse_error(
                raw_text, "レポート内容（report_markdown）が空です。"
            )

        report_html = markdown.markdown(report_md, extensions=["tables", "fenced_code"])
        report_html = bleach.clean(
            report_html,
            tags={
                "p",
                "ul",
                "ol",
                "li",
                "strong",
                "em",
                "code",
                "pre",
                "table",
                "thead",
                "tbody",
                "tr",
                "th",
                "td",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "a",
            },
            attributes={"a": ["href"]},
            protocols={"http", "https", "mailto"},
            strip=True,
        )

        debug_script = build_debug_script(
            extracted_text, regex_summary, raw_text, tool_calls_log
        )

        styled_report = f"""
        <div class="prose max-w-5xl mx-auto bg-paper p-6 rounded-sm border border-stone/30 space-y-6">
            {report_html}
        </div>
        """

        return styled_report + debug_script

    except Exception as e:
        print(f"Error processing upload: {e}")
        safe_error = html.escape(str(e), quote=True)
        return f"""
        <div class="p-4 bg-copper-light/20 border border-copper text-wood-dark rounded-sm">
            <strong>Error Processing Request:</strong><br>
            {safe_error}
        </div>
        """


@router.post("/upload", response_class=HTMLResponse)
async def handle_upload_compat(file: UploadFile = File(...)):
    """Backward compatibility route."""
    return await handle_area_upload(file)
