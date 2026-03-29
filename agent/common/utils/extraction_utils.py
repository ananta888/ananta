import json
import logging
import os
import re
from typing import List, Optional


def extract_command(text: str) -> str:
    """Extrahiert den Shell-Befehl aus dem LLM-Output (JSON oder Markdown)."""
    text = text.strip()

    def fix_json(s: str) -> str:
        s = s.strip()
        if s.startswith("{") and not s.endswith("}"):
            open_braces = s.count("{")
            close_braces = s.count("}")
            if open_braces > close_braces:
                s += "}" * (open_braces - close_braces)
        return s

    # 1. Versuche JSON-Extraktion
    try:
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{"):
            last_brace = text.rfind("}")
            if last_brace != -1:
                json_str = text[: last_brace + 1]
            else:
                json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                data = json.loads(fix_json(json_str))

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
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{"):
            last_brace = text.rfind("}")
            if last_brace != -1:
                json_str = text[: last_brace + 1]
            else:
                json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                if json_str.startswith("{") and not json_str.endswith("}"):
                    json_str += "}" * (json_str.count("{") - json_str.count("}"))
                data = json.loads(json_str)

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
        json_str = ""
        if "```json" in text:
            json_str = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("{") or text.startswith("["):
            json_str = text

        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                if json_str.startswith("{") and not json_str.endswith("}"):
                    json_str += "}" * (json_str.count("{") - json_str.count("}"))
                data = json.loads(json_str)

            if isinstance(data, dict) and "tool_calls" in data:
                return data["tool_calls"]
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return None
