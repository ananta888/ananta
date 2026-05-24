"""ProposePolicyService — loads and merges ProposePolicy for a given context.

Merge precedence: system_default → project → blueprint_role → task_kind_override.
"""
from __future__ import annotations

from typing import Any

from agent.services.propose_policy import (
    ProposePolicy,
    SAFE_DEFAULT_STRATEGY_ORDER,
    build_policy_from_dict,
    get_task_kind_preset,
)
from agent.services.strategy_mode_service import StrategyModeService


class ProposePolicyService:
    """Loads effective ProposePolicy for a task context."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = dict(config or {})
        self._strategy_mode_service = StrategyModeService()

    def get_effective_policy(
        self,
        *,
        task_kind: str | None = None,
        task_override: dict[str, Any] | None = None,
        project_config: dict[str, Any] | None = None,
        blueprint_role_config: dict[str, Any] | None = None,
        admin_overrides: dict[str, Any] | None = None,
    ) -> ProposePolicy:
        """Return merged ProposePolicy for the given context.

        Layers applied in order (later wins for scalar fields, list fields
        are replaced — not merged):
          1. system default
          2. project propose_policy block
          3. blueprint_role propose_policy block
          4. task_kind preset
        """
        merged: dict[str, Any] = self._system_default()

        project_policy = (project_config or {}).get("propose_policy") or {}
        if project_policy:
            merged = self._merge(merged, project_policy)

        role_policy = (blueprint_role_config or {}).get("propose_policy") or {}
        if role_policy:
            merged = self._merge(merged, role_policy)

        preset = get_task_kind_preset(task_kind)
        if preset:
            merged = self._merge(merged, preset)

        # WSM-T001: optional named strategy mode overlay (highest precedence)
        mode_id = (
            (task_override or {}).get("strategy_mode")
            or (blueprint_role_config or {}).get("strategy_mode")
            or (project_config or {}).get("strategy_mode")
            or self._config.get("strategy_mode")
        )
        mode_overrides = self._strategy_mode_service.resolve_policy_overrides(mode_id)
        if mode_overrides:
            merged = self._merge(merged, mode_overrides)
            merged["effective_strategy_mode"] = str(mode_id)

        merged = self._apply_provider_compatibility(
            merged=merged,
            project_config=project_config,
            task_override=task_override,
        )
        merged = self.apply_context_compactor_runtime_profile(
            merged=merged,
            project_config=project_config,
        )

        policy = build_policy_from_dict(merged, admin_overrides=admin_overrides)
        setattr(policy, "effective_strategy_mode", str(merged.get("effective_strategy_mode") or "").strip() or None)
        return policy

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _system_default() -> dict[str, Any]:
        return {
            "strategy_order": list(SAFE_DEFAULT_STRATEGY_ORDER),
            "llm_mode": "primary_with_guardrails",
            "accepted_output_formats": [
                "tool_calls", "strict_json", "fenced_json",
                "shell_block", "unified_diff", "file_blocks", "natural_language",
            ],
            "allow_legacy_sgpt": False,
            "allow_unstructured_text_as_execution": False,
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": True,
            "allow_worker_fallback": True,
            "allow_deterministic_fallback": False,
            "allow_human_review": True,
            "max_strategy_attempts": 1,
            "max_repair_attempts": 1,
            "requires_executable_step": True,
            "on_parse_error": "next_strategy",
            "on_all_strategies_declined": "needs_review",
            "context_compaction_enabled": True,
            "context_compaction_required": False,
            "context_compactor_timeout_seconds": 45,
            "context_compactor_max_output_chars": 12000,
            "context_compactor_retry_attempts": 1,
            "context_compactor_fail_open": False,
            "context_compactor_profile": "default",
            "context_compactor_preserve_keywords": [
                "security",
                "policy",
                "verification",
                "review",
                "constraints",
            ],
        }

    @staticmethod
    def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        result = dict(base)
        for k, v in override.items():
            if v is not None:
                result[k] = v
        return result

    @staticmethod
    def _apply_provider_compatibility(
        merged: dict[str, Any],
        *,
        project_config: dict[str, Any] | None,
        task_override: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = dict(merged or {})
        override_provider = str((task_override or {}).get("provider") or "").strip().lower()
        provider = override_provider
        if not provider:
            provider = str((((project_config or {}).get("llm_config") or {}).get("provider") or "")).strip().lower()
        if provider != "lmstudio":
            return result

        # LMStudio compatibility: avoid strict OpenAI JSON-schema/tool-call contracts first,
        # because many local runtimes reject these response_format/tool constraints.
        order = [str(s).strip() for s in list(result.get("strategy_order") or []) if str(s).strip()]
        preferred = ["flexible_llm_normalization", "tool_calling_llm", "worker_strategy", "advisory_proposal", "human_review"]
        normalized: list[str] = []
        for sid in preferred + order:
            if sid not in normalized:
                normalized.append(sid)
        # Keep json_schema_llm out for LMStudio unless explicitly re-enabled later.
        normalized = [sid for sid in normalized if sid != "json_schema_llm"]
        result["strategy_order"] = normalized
        result["allow_json_schema_fallback"] = False
        result["allow_flexible_normalization"] = True
        return result

    @staticmethod
    def apply_context_compactor_runtime_profile(
        merged: dict[str, Any],
        *,
        project_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = dict(merged or {})
        effective_cfg = dict(project_config or {})
        profile = str(result.get("context_compactor_profile") or "default").strip().lower() or "default"
        runtime_profile = str(effective_cfg.get("runtime_profile") or "").strip().lower()
        if profile == "default" and runtime_profile:
            if "lmstudio" in runtime_profile:
                profile = "lmstudio_laptop"
            elif "ollama" in runtime_profile:
                profile = "ollama_rtx3080"
        if profile == "lmstudio_laptop":
            result["context_compactor_timeout_seconds"] = min(int(result.get("context_compactor_timeout_seconds") or 45), 40)
            result["context_compactor_max_output_chars"] = min(int(result.get("context_compactor_max_output_chars") or 12000), 8000)
            result["context_compactor_retry_attempts"] = min(int(result.get("context_compactor_retry_attempts") or 1), 1)
        elif profile == "ollama_rtx3080":
            result["context_compactor_timeout_seconds"] = min(120, max(45, int(result.get("context_compactor_timeout_seconds") or 60)))
            result["context_compactor_max_output_chars"] = min(24000, max(10000, int(result.get("context_compactor_max_output_chars") or 16000)))
            result["context_compactor_retry_attempts"] = min(3, max(1, int(result.get("context_compactor_retry_attempts") or 2)))
        result["context_compactor_profile"] = profile
        return result


_default_service: ProposePolicyService | None = None


def get_propose_policy_service() -> ProposePolicyService:
    global _default_service
    if _default_service is None:
        _default_service = ProposePolicyService()
    return _default_service
