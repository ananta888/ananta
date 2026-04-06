from __future__ import annotations

import json
import re
from typing import Any

from agent.common.utils.extraction_utils import extract_json_payload

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def sanitize_structured_output_text(raw_text: str) -> str:
    text = _ANSI_RE.sub("", str(raw_text or ""))
    for token in ("<|im_start|>", "<|im_end|>", "<|endoftext|>"):
        text = text.replace(token, "")
    return text.strip()


def normalize_tool_calls(tool_calls: object) -> list[dict] | None:
    if isinstance(tool_calls, list) and all(isinstance(item, dict) for item in tool_calls):
        return tool_calls
    if isinstance(tool_calls, dict):
        return [tool_calls]
    return None


def normalize_structured_action_payload(data: object) -> dict[str, Any] | None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return None
    if isinstance(data, list):
        tool_calls = normalize_tool_calls(data)
        if tool_calls:
            return {"reason": "Recovered tool calls from list payload.", "command": None, "tool_calls": tool_calls}
        return None
    if not isinstance(data, dict):
        return None

    command = None
    for key in ("command", "cmd", "shell_command", "shell", "bash", "script"):
        value = str(data.get(key) or "").strip()
        if value:
            command = value
            break

    tool_calls = None
    for key in ("tool_calls", "toolCalls", "tools", "tool_call"):
        tool_calls = normalize_tool_calls(data.get(key))
        if tool_calls:
            break

    if not command and not tool_calls:
        return None

    reason = ""
    for key in ("reason", "summary", "message", "thought", "explanation"):
        value = str(data.get(key) or "").strip()
        if value:
            reason = value
            break
    if not reason:
        reason = "Recovered structured action from partial model output."

    return {"reason": reason, "command": command, "tool_calls": tool_calls or []}


def parse_structured_action_payload(raw_text: str) -> dict[str, Any] | None:
    sanitized = sanitize_structured_output_text(raw_text)
    candidates: list[str] = []
    for value in (str(raw_text or ""), sanitized):
        if value and value not in candidates:
            candidates.append(value)
        extracted = extract_json_payload(value)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
        stripped_fences = value.replace("```json", "").replace("```", "").strip()
        if stripped_fences and stripped_fences not in candidates:
            candidates.append(stripped_fences)

    for candidate in candidates:
        normalized_candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate.strip())
        if not normalized_candidate:
            continue
        try:
            parsed = json.loads(normalized_candidate)
        except Exception:
            continue
        normalized = normalize_structured_action_payload(parsed)
        if normalized:
            return normalized
    return None


def locally_repair_structured_action_output(raw_text: str) -> str | None:
    payload = parse_structured_action_payload(raw_text)
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False)


def extract_structured_action_fields(raw_text: str) -> tuple[str | None, list[dict] | None]:
    payload = parse_structured_action_payload(raw_text)
    if not payload:
        return None, None
    command = str(payload.get("command") or "").strip() or None
    tool_calls = normalize_tool_calls(payload.get("tool_calls"))
    return command, tool_calls
