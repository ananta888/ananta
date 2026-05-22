from __future__ import annotations

from typing import Any


def resolve_propose_llm_timeout_seconds(*, effective_config: dict[str, Any] | None, task_kind: str | None) -> int:
    cfg = dict(effective_config or {})
    kind = str(task_kind or "").strip().lower()
    task_kind_policies = cfg.get("task_kind_execution_policies") if isinstance(cfg.get("task_kind_execution_policies"), dict) else {}
    task_kind_cfg = task_kind_policies.get(kind) if isinstance(task_kind_policies.get(kind), dict) else {}

    # Prefer explicit LLM timeout override, then propose timeout and command budgets.
    candidates = [
        cfg.get("llm_invoke_timeout_seconds"),
        cfg.get("task_propose_timeout_seconds"),
        cfg.get("command_timeout"),
        task_kind_cfg.get("command_timeout"),
    ]
    parsed: list[int] = []
    for value in candidates:
        try:
            if value is None:
                continue
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return 120
    return max(30, min(max(parsed), 1200))

