from __future__ import annotations

from typing import Any

from agent.services.planning_model_profile_service import get_planning_model_profile_service


class ModelResponseBehaviorProfileService:
    def resolve(self, *, provider: str | None, model_name: str | None) -> dict[str, Any]:
        base = get_planning_model_profile_service().resolve_profile(provider=provider, model_name=model_name)
        return {
            "provider": str(provider or ""),
            "model_name_pattern": base.get("model_name_pattern"),
            "model_family": base.get("model_family"),
            "profile_name": base.get("profile_name"),
            "preferred_parser_chain": base.get("preferred_parser_chain") or [
                "strict_json",
                "strip_markdown_fence",
                "extract_first_json_block",
                "mermaid_graph_extract",
                "llm_repair",
            ],
            "mermaid_handling": base.get("mermaid_handling") or "extract_graph",
            "markdown_handling": base.get("markdown_handling") or "allow_and_strip",
            "json_contract_strength": base.get("output_contract_strictness") or "repair_required",
            "enabled": True,
        }


_SERVICE = ModelResponseBehaviorProfileService()


def get_model_response_behavior_profile_service() -> ModelResponseBehaviorProfileService:
    return _SERVICE
