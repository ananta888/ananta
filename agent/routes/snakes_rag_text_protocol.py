"""Pure text protocol helpers for the RAG Snake loop."""

from __future__ import annotations

import re
from typing import Any


def looks_like_tool_request(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.lower()
    return bool(
        "[tool_request]" in lowered
        or "[end_tool_request]" in lowered
        or re.search(r'"(?:name|tool_name)"\s*:\s*"(?:read_file|search_codebase)"', value)
        or re.search(r'"tool_calls"\s*:', value)
        or re.search(r"\[(?:read_file|search_codebase)\s*\(", value)
    )


def parse_textual_tool_calls(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = (
        ("read_file", "path", r"\[read_file\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\]"),
        ("search_codebase", "query", r"\[search_codebase\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\]"),
    )
    for name, argument_name, pattern in patterns:
        for match in re.finditer(pattern, text):
            key = f"{name}:{match.group(1)}"
            if key not in seen:
                seen.add(key)
                calls.append({"name": name, "args": {argument_name: match.group(1)}})
    return calls


def input_preview(messages: list[dict], max_chars: int = 2000) -> str:
    parts = []
    for message in messages[-4:]:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if content:
            parts.append(f"[{role}]\n{content[:max_chars]}")
    return "\n\n---\n\n".join(parts)


def full_prompt(messages: list[dict]) -> str:
    parts = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if not content and message.get("tool_calls"):
            calls = message["tool_calls"]
            names = [str((call.get("function") or {}).get("name") or "?") for call in calls]
            arguments = [str((call.get("function") or {}).get("arguments") or "") for call in calls]
            content = "\n".join(f"→ tool_call: {name}({args})" for name, args in zip(names, arguments))
        if content:
            parts.append(f"[{role} #{index + 1}]\n{content}")
    return ("\n\n" + "=" * 60 + "\n\n").join(parts)


def total_context_chars(messages: list[dict]) -> int:
    return sum(len(str(message.get("content") or "")) for message in messages)


def parse_file_sections(user_content: str) -> list[dict[str, Any]]:
    marker = "=== Verfügbare Dateien"
    index = user_content.find(marker)
    if index < 0:
        sections = re.split(r"\n### ", "\n" + user_content)
        return [
            {"path": section.partition("\n")[0].strip(), "chars": len(section.partition("\n")[2])}
            for section in sections[1:]
        ]
    block_start = user_content.find("\n", index) + 1
    block_end = user_content.find("\n\n", block_start)
    block = user_content[block_start:block_end if block_end > 0 else block_start + 4000]
    result = []
    for line in block.splitlines():
        match = re.match(r"\s*\d+\.\s+(.+?)\s+\(relevanz:\s*([\d.]+)\)", line)
        if match:
            result.append({"path": match.group(1).strip(), "score": float(match.group(2))})
    return result


def format_evidence_prompt(evidence: dict[str, dict[str, Any]], question: str) -> str:
    if not evidence:
        return ""
    lines = [
        "Recherche-Stand fuer die naechste LLM-Aktion:",
        "Verwende diese fragebezogenen Zusammenfassungen als Arbeitsgedaechtnis.",
        "Bereits gelesene oder im Initialkontext bereitgestellte Dateien:",
    ]
    for index, item in enumerate(evidence.values(), 1):
        score = item.get("score")
        score_text = f", relevanz: {float(score):.1f}" if isinstance(score, int | float) else ""
        lines.append(
            f"{index}. {item['path']} ({item.get('source')}{score_text})\n"
            f"   {item.get('summary')}"
        )
    question_hint = f" Beantworte dann konkret: {question[:200]}" if question else ""
    lines.append(
        "Wenn noch Informationen fehlen, lies gezielt eine weitere Datei, die eine offene Frage klaert. "
        "Nutze search_codebase nur fuer voellig neue Begriffe, die in keiner Evidenz-Datei erwaehnt sind. "
        f"Wenn die Evidenz reicht, antworte jetzt abschliessend.{question_hint}"
    )
    return "\n".join(lines)


def retire_initial_next_step_instruction(messages: list[dict]) -> bool:
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        updated = re.sub(
            r"\nNaechster Schritt: Beginne mit read_file\([^\n]+\) — lies diese Datei als erstes\.",
            "",
            content,
            count=1,
        )
        if updated != content:
            message["content"] = updated
            return True
    return False


def compact_initial_packed_context(messages: list[dict]) -> bool:
    marker = "=== Bereits gelesene CodeCompass-Top-Treffer ==="
    next_marker = "=== Verfügbare Dateien"
    replacement = (
        "=== Bereits gelesene CodeCompass-Top-Treffer (kompakt) ===\n"
        "Die Volltexte wurden fuer Folgeaufrufe entfernt. "
        "Nutze den aktuellen Recherche-Stand in der letzten User-Nachricht.\n\n"
    )
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        start = content.find(marker)
        if start < 0:
            continue
        end = content.find(next_marker, start)
        if end < 0:
            end = start + len(marker)
        message["content"] = content[:start] + replacement + content[end:]
        return True
    return False
