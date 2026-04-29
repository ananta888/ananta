from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from worker.core.execution_profile import normalize_execution_profile, prompt_context_chars_for_profile
from worker.core.redaction import redact_text


def _redact(text: str) -> str:
    return redact_text(str(text or ""))


_INJECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("ignore_previous_instructions", "ignore previous instructions"),
    ("override_system_policy", "override system policy"),
    ("exfiltrate_secret", "exfiltrate"),
    ("dangerous_command_synthesis", "rm -rf /"),
    ("dangerous_command_synthesis", "curl "),
    ("dangerous_command_synthesis", "| sh"),
)


def _detect_context_guardrail_reason(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    for reason, marker in _INJECTION_MARKERS:
        if marker in normalized:
            return reason
    if "system prompt" in normalized or "developer prompt" in normalized:
        return "prompt_hierarchy_override"
    return None


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
    max_context_chars: int | None = None,
    execution_profile: str | None = "balanced",
) -> dict[str, Any]:
    normalized_profile = normalize_execution_profile(execution_profile)
    bounded_context_limit = int(max_context_chars) if max_context_chars is not None else prompt_context_chars_for_profile(normalized_profile)
    if bounded_context_limit <= 0:
        raise ValueError("max_context_chars_must_be_positive")
    normalized_context_hash = str(context_hash).strip()
    if not normalized_context_hash:
        raise ValueError("context_hash_required")
    bounded_chunks: list[str] = []
    blocked_chunks: list[dict[str, Any]] = []
    used_chars = 0
    for chunk in list(context_chunks or []):
        clean = _redact(str(chunk))
        if not clean:
            continue
        guardrail_reason = _detect_context_guardrail_reason(clean)
        if guardrail_reason:
            blocked_chunks.append({"reason": guardrail_reason, "preview": clean[:160]})
            continue
        remaining = bounded_context_limit - used_chars
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
    guardrail_summary = (
        f"- blocked_context_chunks={len(blocked_chunks)}"
        if blocked_chunks
        else "- blocked_context_chunks=0"
    )
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
        f"Bounded context (max {bounded_context_limit} chars):\n{context_text if context_text else '- none'}\n\n"
        f"Context guardrails:\n{guardrail_summary}\n\n"
        "Never assume unbounded full-repo context."
    )
    return {
        "prompt": prompt,
        "prompt_metadata": {
            "prompt_template_version": prompt_template_version,
            "context_hash": normalized_context_hash,
            "execution_profile": normalized_profile,
            "assembled_at": datetime.now(UTC).isoformat(),
            "bounded_context_chars": used_chars,
            "max_context_chars": int(bounded_context_limit),
            "selected_file_count": len(selected_files),
            "blocked_context_chunks": len(blocked_chunks),
            "context_guard_status": "degraded" if blocked_chunks else "ok",
            "blocked_context_reasons": sorted({str(item.get("reason") or "") for item in blocked_chunks if item.get("reason")}),
        },
    }
