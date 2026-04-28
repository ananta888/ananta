from __future__ import annotations

import json
import re
import shlex
from typing import Any

from agent.common.utils.extraction_utils import extract_json_payload

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_BARE_JSON_KEY_RE = re.compile(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:)')
_COMMAND_KEY_RE = re.compile(r'"(?:command|cmd|shell_command|shell|bash|script)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_ARGS_KEY_RE = re.compile(r'"(?:args|arguments|argv)"\s*:\s*\[(.*?)\]', re.DOTALL)
_JSON_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_REASON_KEY_RE = re.compile(r'"(?:reason|summary|message|thought|explanation)"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)


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


def _decode_json_string(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except Exception:
        return value


def _normalize_command_with_args(command: str, args: object) -> str:
    base_command = str(command or "").strip()
    if not base_command:
        return ""
    if not isinstance(args, list):
        return base_command
    normalized_args = [str(item).strip() for item in args if isinstance(item, str) and str(item).strip()]
    if not normalized_args:
        return base_command
    return " ".join([base_command, *[shlex.quote(item) for item in normalized_args]])


def _fallback_extract_command_payload(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "")
    command_match = _COMMAND_KEY_RE.search(text)
    if not command_match:
        return None
    command = _decode_json_string(command_match.group(1)).strip()
    if not command:
        return None

    args: list[str] = []
    args_match = _ARGS_KEY_RE.search(text)
    if args_match:
        for string_match in _JSON_STRING_RE.finditer(args_match.group(1)):
            arg_value = _decode_json_string(string_match.group(1)).strip()
            if arg_value:
                args.append(arg_value)
    command = _normalize_command_with_args(command, args)

    reason = ""
    reason_match = _REASON_KEY_RE.search(text)
    if reason_match:
        reason = _decode_json_string(reason_match.group(1)).strip()
    if not reason:
        reason = "Recovered structured action from fallback command parser."
    return {"reason": reason, "command": command, "tool_calls": []}


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
    if command:
        command = _normalize_command_with_args(command, data.get("args") or data.get("arguments") or data.get("argv"))

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


def _json_like_variants(candidate: str) -> list[str]:
    stripped = candidate.strip()
    if not stripped:
        return []
    variants: list[str] = []

    def _add(value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in variants:
            variants.append(normalized)

    _add(stripped)
    repaired = _TRAILING_COMMA_RE.sub(r"\1", stripped)
    _add(repaired)
    quoted_keys = _BARE_JSON_KEY_RE.sub(r'\1"\2"\3', repaired)
    _add(quoted_keys)
    if quoted_keys.endswith('"') and "}" in quoted_keys and quoted_keys.rfind("}") < len(quoted_keys) - 1:
        _add(quoted_keys[: quoted_keys.rfind("}") + 1])
    if repaired.endswith('"') and "}" in repaired and repaired.rfind("}") < len(repaired) - 1:
        _add(repaired[: repaired.rfind("}") + 1])
    return variants


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
        for normalized_candidate in _json_like_variants(candidate):
            try:
                parsed = json.loads(normalized_candidate)
            except Exception:
                continue
            normalized = normalize_structured_action_payload(parsed)
            if normalized:
                return normalized
    for candidate in candidates:
        fallback_payload = _fallback_extract_command_payload(candidate)
        if fallback_payload:
            return fallback_payload
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
