from __future__ import annotations

import json
from typing import Any


class StrategyPromptComposer:
    """Compose deterministic system prompts from instruction stack + strategy contract."""

    @staticmethod
    def _append_unique(parts: list[str], text: str | None) -> None:
        value = str(text or "").strip()
        if not value:
            return
        if value in parts:
            return
        parts.append(value)

    def compose_system_prompt(
        self,
        *,
        context,
        prompt_context_bundle: dict[str, Any],
        strategy_contract: dict[str, Any],
        governed_context_summary: str | None = None,
    ) -> str:
        task = context.task or {}
        task_desc = (task.get("description") or task.get("prompt") or "").strip()
        parts: list[str] = []
        self._append_unique(parts, str(strategy_contract.get("role") or "").strip())
        self._append_unique(parts, f"Goal: {context.goal_id}")
        self._append_unique(parts, f"Task: {context.task_id}")
        self._append_unique(parts, f"Task kind: {task.get('task_kind') or 'unknown'}")
        self._append_unique(parts, str(getattr(context, "rendered_system_prompt", None) or "").strip())
        if task_desc and len(task_desc) > 20:
            self._append_unique(parts, "Task description:")
            self._append_unique(parts, task_desc)
        if governed_context_summary:
            self._append_unique(parts, "Governed context summary:")
            self._append_unique(parts, governed_context_summary)
        self._append_unique(parts, "Prompt context bundle:")
        self._append_unique(parts, json.dumps(prompt_context_bundle, ensure_ascii=True, sort_keys=True))
        self._append_unique(parts, str(strategy_contract.get("output_contract") or "").strip())
        return "\n\n".join(item for item in parts if item)


_service = StrategyPromptComposer()


def get_strategy_prompt_composer() -> StrategyPromptComposer:
    return _service

