from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

_TOKEN_PATTERN = re.compile(r"\b(?:ghp|gho|sk|api)[A-Za-z0-9_-]{10,}\b")


def _redact(text: str) -> str:
    return _TOKEN_PATTERN.sub("[REDACTED_TOKEN]", str(text or ""))


def assemble_coding_prompt(
    *,
    task: dict[str, Any],
    constraints: dict[str, Any],
    selected_files: list[dict[str, Any]],
    relevant_symbols: list[str],
    policy: dict[str, Any],
    expected_output_schema: dict[str, Any],
    forbidden_actions: list[str],
    context_hash: str,
    context_chunks: list[str] | None = None,
    prompt_template_version: str = "worker_coding_prompt_v1",
    max_context_chars: int = 8000,
) -> dict[str, Any]:
    normalized_context_hash = str(context_hash).strip()
    if not normalized_context_hash:
        raise ValueError("context_hash_required")
    bounded_chunks: list[str] = []
    used_chars = 0
    for chunk in list(context_chunks or []):
        clean = _redact(str(chunk))
        if not clean:
            continue
        remaining = max_context_chars - used_chars
        if remaining <= 0:
            break
        bounded = clean[:remaining]
        bounded_chunks.append(bounded)
        used_chars += len(bounded)
    file_lines = [
        f"- {entry.get('path')} | symbol={entry.get('symbol') or '-'} | reason={entry.get('reason') or 'selection'}"
        for entry in selected_files
    ]
    symbol_lines = [f"- {sym}" for sym in relevant_symbols if str(sym).strip()]
    forbidden_lines = [f"- {item}" for item in forbidden_actions if str(item).strip()]
    policy_summary = _redact(str(policy))
    constraint_summary = _redact(str(constraints))
    task_text = _redact(str(task))
    expected_schema_text = _redact(str(expected_output_schema))
    context_text = "\n".join(f"- {chunk}" for chunk in bounded_chunks)
    prompt = (
        "You are Ananta native coding worker.\n"
        "Follow constraints, policy and output schema strictly.\n\n"
        f"Task:\n{task_text}\n\n"
        f"Constraints:\n{constraint_summary}\n\n"
        f"Selected files:\n{chr(10).join(file_lines) if file_lines else '- none'}\n\n"
        f"Relevant symbols:\n{chr(10).join(symbol_lines) if symbol_lines else '- none'}\n\n"
        f"Policy summary:\n{policy_summary}\n\n"
        f"Expected output schema:\n{expected_schema_text}\n\n"
        f"Forbidden actions:\n{chr(10).join(forbidden_lines) if forbidden_lines else '- none'}\n\n"
        f"Bounded context (max {max_context_chars} chars):\n{context_text if context_text else '- none'}\n\n"
        "Never assume unbounded full-repo context."
    )
    return {
        "prompt": prompt,
        "prompt_metadata": {
            "prompt_template_version": prompt_template_version,
            "context_hash": normalized_context_hash,
            "assembled_at": datetime.now(UTC).isoformat(),
            "bounded_context_chars": used_chars,
            "max_context_chars": int(max_context_chars),
            "selected_file_count": len(selected_files),
        },
    }
