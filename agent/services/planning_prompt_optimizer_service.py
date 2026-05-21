from __future__ import annotations

from typing import Any


class PlanningPromptOptimizerService:
    def optimize(
        self,
        *,
        prompt: str,
        preferred_output_format: str | None = None,
        behavior_profile: dict[str, Any] | None,
    ) -> tuple[str, str]:
        profile = dict(behavior_profile or {})
        style = str(profile.get("preferred_prompt_style") or "strict_json_minimal").strip() or "strict_json_minimal"
        text = str(prompt or "")
        output_format = str(preferred_output_format or "").strip().lower()
        learning_state = dict(profile.get("learning_state") or {})
        observed_shape = str(
            profile.get("observed_output_shape")
            or learning_state.get("observed_output_shape")
            or learning_state.get("observed_output_format")
            or ""
        ).strip().lower()

        extra_rules: list[str] = []
        if observed_shape in {"json_in_markdown_fence", "markdown_bullets", "numbered_steps", "yaml_like"}:
            extra_rules.append("Adapt to the model's observed output style during the learning phase instead of forcing a rigid one.")
            if observed_shape == "json_in_markdown_fence":
                extra_rules.append("JSON wrapped in markdown fences is acceptable if the JSON remains complete and parseable.")
            elif observed_shape in {"markdown_bullets", "numbered_steps"}:
                extra_rules.append("Markdown lists are acceptable; keep each item concrete and normalizable.")
            elif observed_shape == "yaml_like":
                extra_rules.append("YAML-like key/value output is acceptable if it stays short and unambiguous.")
        if str(learning_state.get("state") or "").strip().lower() in {"learning", "candidate"}:
            extra_rules.append("Treat the current model output style as a learning signal and prefer the least disruptive repair path.")
        if style in {"no_markdown_explicit", "json_schema_first"} or str(profile.get("markdown_handling") or "") == "forbid":
            extra_rules.append("Do not use markdown fences. Output plain JSON only.")
        if style in {"mermaid_forbidden"} or str(profile.get("mermaid_handling") or "") in {"forbid_in_prompt", "ignore"}:
            extra_rules.append("Do not output Mermaid diagrams unless explicitly requested.")
        if style in {"example_driven_json", "stepwise_then_json"}:
            extra_rules.append('Return a JSON array of tasks, e.g. [{"title":"...","description":"...","priority":"High"}]')
        if style in {"markdown_friendly"}:
            extra_rules.append("If markdown is more natural for this model, keep markdown concise and map it back to normalized tasks later.")
        if output_format == "yaml":
            extra_rules.append("Output YAML only. Do not output JSON or markdown fences.")
        elif output_format == "markdown":
            extra_rules.append("Output markdown bullet list only. One actionable task per bullet.")
        elif output_format == "json":
            extra_rules.append("Output strict JSON only. No markdown fences, no prose.")

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
