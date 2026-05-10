from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.core.context_resolver import ContextBlock, ContextSensitivity


_SENSITIVE_BLOCKS = frozenset({ContextSensitivity.secret, ContextSensitivity.confidential})


@dataclass
class ContextConversionResult:
    prompt_text: str
    included: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    truncated: list[dict[str, Any]] = field(default_factory=list)
    total_chars: int = 0
    has_required_context: bool = True


def convert_context_blocks_to_prompt(
    blocks: list[ContextBlock],
    *,
    max_context_chars: int,
    allow_sensitive: bool,
) -> ContextConversionResult:
    lines: list[str] = []
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    truncated: list[dict[str, Any]] = []
    total = 0

    for block in blocks:
        if block.sensitivity in _SENSITIVE_BLOCKS and not allow_sensitive:
            skipped.append({"origin_id": block.origin_id, "reason_code": "sensitive_block_excluded"})
            continue
        content = str(block.content or "")
        if not content.strip():
            skipped.append({"origin_id": block.origin_id, "reason_code": "empty_context_block"})
            continue

        entry = f"[source={block.source_type}:{block.origin_id}] {content}"
        if total + len(entry) > max_context_chars:
            remaining = max_context_chars - total
            if remaining <= 0:
                truncated.append({"origin_id": block.origin_id, "reason_code": "budget_exhausted"})
                continue
            entry = entry[:remaining]
            truncated.append({"origin_id": block.origin_id, "reason_code": "truncated_for_budget"})
        lines.append(entry)
        included.append({"origin_id": block.origin_id, "reason_code": "included"})
        total += len(entry)
        if total >= max_context_chars:
            break

    return ContextConversionResult(
        prompt_text="\n".join(lines).strip(),
        included=included,
        skipped=skipped,
        truncated=truncated,
        total_chars=total,
        has_required_context=bool(included),
    )

