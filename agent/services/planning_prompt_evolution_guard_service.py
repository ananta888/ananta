from __future__ import annotations

from typing import Any

_FORBIDDEN_KEYS = {
    "allowed_tools",
    "tool_permissions",
    "shell_allowed",
    "cloud_allowed",
    "context_scope",
    "security_policy",
    "governance_policy",
    "approval_policy",
    "worker_system_prompt_patch",
    "role_template_patch",
    "overlay_patch",
    "provider_override",
    "ignore_governance",
}


class PlanningPromptEvolutionGuardService:
    """Guards planning prompt mutations against scope and policy violations."""

    def validate_mutation(self, *, payload: dict[str, Any]) -> tuple[bool, list[str]]:
        violations: list[str] = []
        output_contract = dict(payload.get("output_contract") or {})
        system_rules = list(payload.get("system_rules") or [])
        user_prompt = str(payload.get("user_prompt_template") or "")
        repair_prompt = str(payload.get("repair_prompt_template") or "")

        blocked_paths = self._find_forbidden_keys(output_contract)
        violations.extend([f"forbidden_output_contract_key:{path}" for path in blocked_paths])
        for rule in system_rules:
            rule_text = str(rule or "").strip().lower()
            for key in _FORBIDDEN_KEYS:
                if key in rule_text:
                    violations.append(f"forbidden_system_rule:{key}")
        prompt_text = f"{user_prompt}\n{repair_prompt}".lower()
        for key in ("ignore_governance", "worker_system_prompt_patch", "role_template_patch", "overlay_patch", "allowed_tools"):
            if key in prompt_text:
                violations.append(f"forbidden_prompt_directive:{key}")
        return len(violations) == 0, sorted(set(violations))

    def _find_forbidden_keys(self, payload: dict[str, Any]) -> list[str]:
        blocked: list[str] = []

        def _walk(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    key_s = str(key or "").strip()
                    path = f"{prefix}.{key_s}" if prefix else key_s
                    if key_s.lower() in _FORBIDDEN_KEYS:
                        blocked.append(path)
                    _walk(path, nested)
            elif isinstance(value, list):
                for idx, nested in enumerate(value):
                    _walk(f"{prefix}[{idx}]", nested)

        _walk("", dict(payload or {}))
        return blocked


_SERVICE = PlanningPromptEvolutionGuardService()


def get_planning_prompt_evolution_guard_service() -> PlanningPromptEvolutionGuardService:
    return _SERVICE

