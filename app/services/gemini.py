"""Gemini (Vertex AI) model interaction: JSON extraction, response parsing, tool calls."""

import json

from google.genai import types

from extractors.tool_definitions import TOOLS, SKILL_REGISTRY

MAX_TOOL_CALL_ITERATIONS = 6


def extract_json(raw_text):
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
        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None


def get_function_calls(response):
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


def get_response_text(response):
    """Get full text from the model response. Uses parts when response.text is empty or raises.
    Gemini may return mixed parts (e.g. thought + text or function_call + text); .text can then
    be empty or raise ValueError. This helper concatenates text from all text parts.
    """
    try:
        t = getattr(response, "text", None)
        if t and isinstance(t, str) and t.strip():
            return t
    except (ValueError, AttributeError, TypeError):
        pass
    out = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text and isinstance(text, str):
                out.append(text)
    return "".join(out).strip() if out else ""


def execute_function_call(func_call):
    name = getattr(func_call, "name", "")
    args = getattr(func_call, "args", None) or {}
    handler = SKILL_REGISTRY.get(name)
    if not handler:
        return name, args, {"error": f"Unknown tool: {name}"}
    try:
        result = handler(**args)
        return name, args, {"result": result}
    except Exception as exc:
        return name, args, {"error": str(exc)}


def generate_with_tools(client, model_name, parts, generation_config):
    """Handle chat + tool execution loop with the google-genai models API.

    Returns:
        tuple: (response, tool_calls_log) where tool_calls_log is a list of dicts
               containing name, args, and result for each tool call.
    """
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

    tool_calls_log = []

    for _ in range(MAX_TOOL_CALL_ITERATIONS):
        func_calls = get_function_calls(response)
        if not func_calls:
            break

        tool_responses = []
        for func_call in func_calls:
            name, args, payload = execute_function_call(func_call)
            tool_part = types.Part.from_function_response(name=name, response=payload)
            tool_responses.append(tool_part)

            tool_calls_log.append(
                {
                    "name": name,
                    "args": dict(args) if args else {},
                    "result": payload,
                }
            )
            print(f"[Tool Call] {name}({args}) -> {payload}")

        candidates = getattr(response, "candidates", None) or []
        if not candidates or not getattr(candidates[0], "content", None):
            raise RuntimeError(
                "Model response contained no candidates/content during tool loop; "
                "cannot continue tool-based generation."
            )
        messages.append(candidates[0].content)
        messages.append(types.Content(role="user", parts=tool_responses))

        response = client.models.generate_content(
            model=model_name,
            contents=messages,
            config=config,
        )
    return response, tool_calls_log
