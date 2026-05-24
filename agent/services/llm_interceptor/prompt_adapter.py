from __future__ import annotations

from typing import Any


class PromptAdapter:
    """Prepends approved policy messages and model formatting hints."""

    def __init__(self, profiles: dict[str, dict[str, Any]] | None = None) -> None:
        self.profiles = dict(profiles or {})

    def _profile_for(self, model: str, task_kind: str | None = None) -> dict[str, Any]:
        task = str(task_kind or "").strip().lower()
        exact = self.profiles.get(model) or {}
        if task and isinstance(exact.get("task_overrides"), dict):
            merged = dict(exact)
            merged.update(dict(exact["task_overrides"].get(task) or {}))
            return merged
        return dict(exact)

    def adapt_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        task_kind: str | None = None,
        require_strict_json: bool = False,
    ) -> list[dict[str, Any]]:
        original = [dict(m) for m in list(messages or [])]
        profile = self._profile_for(model, task_kind=task_kind)
        prepend: list[dict[str, Any]] = []
        policy_text = str(profile.get("policy_preamble") or "").strip()
        if policy_text:
            prepend.append({"role": "system", "content": policy_text})
        if require_strict_json or bool(profile.get("markdown_prone", False)):
            prepend.append(
                {
                    "role": "system",
                    "content": "Return valid JSON only. No markdown fences. Keep schema exact.",
                }
            )
        return prepend + original

