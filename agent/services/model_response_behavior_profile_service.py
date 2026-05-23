from __future__ import annotations

from typing import Any

from agent.services.planning_model_profile_service import get_planning_model_profile_service


class ModelResponseBehaviorProfileService:
    @staticmethod
    def _derive_preferred_prompt_style(*, learning_state: dict[str, Any], base: dict[str, Any]) -> str:
        state = str((learning_state or {}).get("state") or "").strip().lower()
        observed_shape = str(
            (learning_state or {}).get("observed_output_shape")
            or (learning_state or {}).get("observed_output_format")
            or base.get("observed_output_shape")
            or base.get("observed_output_format")
            or ""
        ).strip().lower()
        preferred_format = str(base.get("preferred_output_format") or "").strip().lower()

        if observed_shape in {"json_in_markdown_fence", "markdown_bullets", "numbered_steps"}:
            return "stepwise_then_json" if observed_shape != "json_in_markdown_fence" else "example_driven_json"
        if observed_shape in {"yaml_like"}:
            return "yaml_first"
        if observed_shape in {"partial_json"}:
            return "stepwise_then_json"
        if preferred_format == "markdown":
            return "markdown_friendly"
        if state in {"candidate", "learning"}:
            return "example_driven_json"
        return "strict_json_minimal"

    def resolve(self, *, provider: str | None, model_name: str | None) -> dict[str, Any]:
        base = get_planning_model_profile_service().resolve_profile(provider=provider, model_name=model_name)
        learning_state = dict(base.get("learning_state") or {})
        preferred_output_format = str(base.get("preferred_output_format") or "json").strip().lower() or "json"
        preferred_prompt_style = self._derive_preferred_prompt_style(learning_state=learning_state, base=base)
        markdown_handling = "allow_and_strip" if preferred_output_format == "markdown" or preferred_prompt_style in {"markdown_friendly", "stepwise_then_json"} else "forbid"
        observed_output_shape = str(
            learning_state.get("observed_output_shape")
            or learning_state.get("observed_output_format")
            or base.get("observed_output_shape")
            or base.get("observed_output_format")
            or ""
        ).strip().lower() or None
        return {
            "provider": str(provider or ""),
            "model_name_pattern": base.get("model_name_pattern"),
            "model_family": base.get("model_family"),
            "profile_name": base.get("profile_name"),
            "learning_state": learning_state,
            "preferred_parser_chain": base.get("preferred_parser_chain") or [
                "strict_json",
                "strip_markdown_fence",
                "extract_first_json_block",
                "mermaid_graph_extract",
                "llm_repair",
            ],
            "mermaid_handling": base.get("mermaid_handling") or "extract_graph",
            "markdown_handling": base.get("markdown_handling") or markdown_handling,
            "json_contract_strength": base.get("output_contract_strictness") or "repair_required",
            "preferred_output_format": preferred_output_format,
            "preferred_prompt_style": preferred_prompt_style,
            "observed_output_shape": observed_output_shape,
            "enabled": True,
        }


_SERVICE = ModelResponseBehaviorProfileService()


def get_model_response_behavior_profile_service() -> ModelResponseBehaviorProfileService:
    return _SERVICE
