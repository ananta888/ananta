from __future__ import annotations

from typing import Any


class PlanningPromptOptimizerService:
    def optimize(self, *, prompt: str, behavior_profile: dict[str, Any] | None) -> tuple[str, str]:
        profile = dict(behavior_profile or {})
        style = str(profile.get("preferred_prompt_style") or "strict_json_minimal").strip() or "strict_json_minimal"
        text = str(prompt or "")

        extra_rules: list[str] = []
        if style in {"no_markdown_explicit", "json_schema_first"} or str(profile.get("markdown_handling") or "") == "forbid":
            extra_rules.append("Do not use markdown fences. Output plain JSON only.")
        if style in {"mermaid_forbidden"} or str(profile.get("mermaid_handling") or "") in {"forbid_in_prompt", "ignore"}:
            extra_rules.append("Do not output Mermaid diagrams unless explicitly requested.")
        if style in {"example_driven_json", "stepwise_then_json"}:
            extra_rules.append('Return a JSON array of tasks, e.g. [{"title":"...","description":"...","priority":"High"}]')

        if not extra_rules:
            return text, style
        if "ANFORDERUNGEN:" in text:
            text = text + "\n" + "\n".join(f"- {rule}" for rule in extra_rules)
        else:
            text = text + "\n\n" + "\n".join(extra_rules)
        return text, style


_SERVICE = PlanningPromptOptimizerService()


def get_planning_prompt_optimizer_service() -> PlanningPromptOptimizerService:
    return _SERVICE
