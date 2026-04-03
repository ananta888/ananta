from __future__ import annotations

import json

from flask import Response, stream_with_context


def extract_json(text: str) -> dict | None:
    clean_text = text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text.split("```json")[1].split("```")[0].strip()
    elif clean_text.startswith("```"):
        clean_text = clean_text.split("```")[1].split("```")[0].strip()
    if clean_text.lower().startswith("assistant:"):
        clean_text = clean_text.split(":", 1)[1].strip()
    first_brace = clean_text.find("{")
    first_bracket = clean_text.find("[")
    if first_brace == -1 and first_bracket == -1:
        return None
    if first_brace == -1:
        start = first_bracket
        end = clean_text.rfind("]")
    elif first_bracket == -1:
        start = first_brace
        end = clean_text.rfind("}")
    else:
        start = min(first_brace, first_bracket)
        end = clean_text.rfind("}" if start == first_brace else "]")
    if end == -1:
        return None
    try:
        return json.loads(clean_text[start : end + 1].strip())
    except Exception:
        return None


def build_system_instruction(*, tools_desc: str, context, stream: bool) -> str:
    instruction = (
        "Du bist ein hilfreicher KI-Assistent für das Ananta Framework.\n"
        f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n"
    )
    if context:
        instruction += f"\nAktueller Kontext (Templates, Rollen, Teams):\n{json.dumps(context, indent=2, ensure_ascii=False)}\n"
    instruction += (
        "\nWenn du eine Aktion ausführen möchtest, antworte AUSSCHLIESSLICH im folgenden JSON-Format.\n"
        "Beginne die Antwort mit '{' und ende mit '}'. Keine Vor- oder Nachtexte, kein Markdown, kein Prefix wie 'Assistant:'.\n"
        "{\n"
        '  "thought": "Deine Überlegung, warum du dieses Tool wählst",\n'
        '  "tool_calls": [\n'
        '    { "name": "tool_name", "args": { "arg1": "value1" } }\n'
        "  ],\n"
        '  "answer": "Eine kurze Bestätigung für den Nutzer, was du tust"\n'
        "}\n\n"
        "Falls keine Aktion nötig ist, antworte ebenfalls als JSON-Objekt mit leerem tool_calls.\n\n"
    )
    if stream:
        instruction += "\nAntworte im Streaming-Modus als Klartext ohne tool_calls oder JSON.\n"
    return instruction


def build_sse_response(text: str):
    def _event_stream():
        chunk_size = 80
        for index in range(0, len(text), chunk_size):
            yield f"data: {text[index:index + chunk_size]}\n\n"
        yield "event: done\ndata: [DONE]\n\n"

    return Response(stream_with_context(_event_stream()), mimetype="text/event-stream")
