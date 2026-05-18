from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class WorkspaceContextPolicy:
    scope_mode: str = "full"
    allowed_paths: tuple[str, ...] = field(default_factory=tuple)
    codecompass_profile: Optional[str] = None
    max_files: int = 200
    sensitivity_ceiling: str = "confidential"
    resolution_trace: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.allowed_paths, list):
            object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        scope = str(self.scope_mode or "").strip().lower()
        if scope not in {"full", "none", "selective"}:
            raise ValueError(f"invalid scope_mode: {self.scope_mode}")
        if int(self.max_files) < 0:
            raise ValueError("max_files must be >= 0")


_SYSTEM_DEFAULT = WorkspaceContextPolicy(
    scope_mode="full",
    allowed_paths=(),
    codecompass_profile=None,
    max_files=200,
    sensitivity_ceiling="confidential",
)

_TASK_KIND_DEFAULTS: dict[str, dict] = {
    "coding": {"scope_mode": "selective", "codecompass_profile": "subtask_refactor_navigation"},
    "refactor": {"scope_mode": "selective", "codecompass_profile": "subtask_refactor_navigation"},
    "implement": {"scope_mode": "selective", "codecompass_profile": "subtask_refactor_navigation"},
    "analysis": {"scope_mode": "selective", "codecompass_profile": "subtask_architecture_review"},
    "doc": {"scope_mode": "selective", "codecompass_profile": "subtask_architecture_review"},
    "research": {"scope_mode": "selective", "codecompass_profile": "subtask_architecture_review"},
    "bugfix": {"scope_mode": "selective", "codecompass_profile": "subtask_bugfix_local"},
    "test": {"scope_mode": "selective", "codecompass_profile": "subtask_bugfix_local"},
    "testing": {"scope_mode": "selective", "codecompass_profile": "subtask_bugfix_local"},
    "config": {"scope_mode": "selective", "codecompass_profile": "subtask_config_integration"},
    "xml": {"scope_mode": "selective", "codecompass_profile": "subtask_config_integration"},
    "ops": {"scope_mode": "selective", "codecompass_profile": "subtask_config_integration"},
}


def _merge_policy(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if v is not None:
            merged[k] = v
    return merged


class WorkspaceContextPolicyResolver:
    def __init__(self, template_registry=None) -> None:
        self._registry = template_registry

    def resolve(
        self,
        goal_config: dict,
        task_kind: Optional[str],
        agent_template: Optional[str],
    ) -> WorkspaceContextPolicy:
        resolved: dict = {
            "scope_mode": _SYSTEM_DEFAULT.scope_mode,
            "allowed_paths": list(_SYSTEM_DEFAULT.allowed_paths),
            "codecompass_profile": _SYSTEM_DEFAULT.codecompass_profile,
            "max_files": _SYSTEM_DEFAULT.max_files,
            "sensitivity_ceiling": _SYSTEM_DEFAULT.sensitivity_ceiling,
        }
        trace: dict[str, str] = {
            "scope_mode": "system_default",
            "allowed_paths": "system_default",
            "codecompass_profile": "system_default",
            "max_files": "system_default",
            "sensitivity_ceiling": "system_default",
        }

        kind = str(task_kind or "").strip().lower()
        if kind in _TASK_KIND_DEFAULTS:
            resolved = _merge_policy(resolved, _TASK_KIND_DEFAULTS[kind])
            for key in _TASK_KIND_DEFAULTS[kind].keys():
                trace[key] = "task_kind"

        if agent_template and self._registry:
            tmpl_defaults = self._registry.get_context_policy_defaults(agent_template)
            if tmpl_defaults:
                resolved = _merge_policy(resolved, tmpl_defaults)
                for key in tmpl_defaults.keys():
                    trace[key] = "agent_template"

        goal_policy = dict((goal_config or {}).get("workspace_context_policy") or {})
        if goal_policy:
            resolved = _merge_policy(resolved, goal_policy)
            for key in goal_policy.keys():
                trace[key] = "goal_config"

        return WorkspaceContextPolicy(
            scope_mode=str(resolved.get("scope_mode") or "full"),
            allowed_paths=tuple(resolved.get("allowed_paths") or []),
            codecompass_profile=resolved.get("codecompass_profile"),
            max_files=int(resolved.get("max_files") or 200),
            sensitivity_ceiling=str(resolved.get("sensitivity_ceiling") or "confidential"),
            resolution_trace=trace,
        )


_resolver_instance: Optional[WorkspaceContextPolicyResolver] = None


def get_workspace_context_policy_resolver() -> WorkspaceContextPolicyResolver:
    global _resolver_instance
    if _resolver_instance is None:
        from agent.services.agent_template_registry import get_agent_template_registry
        _resolver_instance = WorkspaceContextPolicyResolver(
            template_registry=get_agent_template_registry()
        )
    return _resolver_instance
