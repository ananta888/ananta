import json
import logging
import os
import re
from typing import Any, List, Optional


def _strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].startswith("```"):
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    return cleaned.strip()


def _load_json_candidate(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        if payload.startswith("{") and payload.count("{") > payload.count("}"):
            return json.loads(payload + ("}" * (payload.count("{") - payload.count("}"))))
        raise


def extract_json_payload(text: str) -> str | None:
    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    decoder = json.JSONDecoder()
    candidates = list(re.findall(r"```json\s*(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL))
    cleaned = _strip_markdown_fences(raw_text)
    if cleaned:
        candidates.append(cleaned)
    for candidate in candidates:
        snippet = str(candidate or "").strip()
        if not snippet:
            continue
        for index, char in enumerate(snippet):
            if char not in "{[":
                continue
            fragment = snippet[index:]
            try:
                _, end = decoder.raw_decode(fragment)
                return fragment[:end].strip()
            except json.JSONDecodeError:
                try:
                    parsed = _load_json_candidate(fragment)
                except Exception:
                    continue
                if isinstance(parsed, (dict, list)):
                    return json.dumps(parsed, ensure_ascii=False)
    return None


def extract_command(text: str) -> str:
    """Extrahiert den Shell-Befehl aus dem LLM-Output (JSON oder Markdown)."""
    text = text.strip()

    # 1. Versuche JSON-Extraktion
    try:
        json_str = extract_json_payload(text)
        if json_str:
            data = _load_json_candidate(json_str)
            if isinstance(data, dict) and "command" in data:
                return str(data["command"]).strip()
    except Exception:
        pass

    # 2. Fallback auf Markdown Code-Blöcke
    for lang in ["bash", "sh", "shell", "powershell", "ps1", "cmd"]:
        pattern = rf"```(?:{lang})\n(.*?)\n```"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            content = parts[1].strip()
            if content and "\n" in content and not content.startswith((" ", "\t")):
                lines = content.split("\n")
                if len(lines) > 1 and len(lines[0].split()) == 1:
                    return "\n".join(lines[1:]).strip()
            return content

    return text.strip()


def extract_reason(text: str) -> str:
    """Extrahiert die Begründung (JSON 'reason' oder Text vor dem Code-Block)."""
    text = text.strip()
    try:
        json_str = extract_json_payload(text)
        if json_str:
            data = _load_json_candidate(json_str)
            if isinstance(data, dict):
                for key in ["reason", "thought", "explanation", "begründung"]:
                    if key in data:
                        return str(data[key]).strip()
    except Exception:
        pass

    if "```" in text:
        reason = text.split("```")[0].strip()
        if reason:
            return reason

    if len(text) > 0:
        return text[:200] + "..." if len(text) > 200 else text

    return "Keine Begründung angegeben."


def extract_tool_calls(text: str) -> Optional[List[dict]]:
    """Extrahiert tool_calls aus dem LLM-Output."""
    text = text.strip()
    try:
        json_str = extract_json_payload(text)
        if json_str:
            data = _load_json_candidate(json_str)
            if isinstance(data, dict) and "tool_calls" in data:
                return data["tool_calls"]
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return None
