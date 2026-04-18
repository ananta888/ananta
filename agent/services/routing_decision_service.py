from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_DEFAULT_FALLBACK_POLICY = {
    "enabled": True,
    "allow_static_providers": True,
    "allow_local_backends": True,
    "allow_remote_hubs": True,
    "allow_stateful_cli": True,
    "allow_stateless_generation": True,
    "fallback_order": [
        "request_override",
        "task_benchmark",
        "configured_default",
        "local_runtime_probe",
    ],
    "unavailable_action": "mark_unavailable",
}

_ALLOWED_FALLBACK_STEPS = {
    "request_override",
    "task_benchmark",
    "configured_default",
    "local_runtime_probe",
    "stateful_cli",
    "stateless_generation",
    "remote_hub",
}


@dataclass(frozen=True)
class RoutingDecisionChain:
    policy_version: str
    task_kind: str | None
    steps: list[dict[str, Any]]
    effective: dict[str, Any]
    fallback_policy: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "task_kind": self.task_kind,
            "steps": list(self.steps),
            "effective": dict(self.effective),
            "fallback_policy": dict(self.fallback_policy),
        }


def _normalize_step_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return list(_DEFAULT_FALLBACK_POLICY["fallback_order"])
    steps = []
    for item in raw:
        step = str(item or "").strip()
        if step in _ALLOWED_FALLBACK_STEPS and step not in steps:
            steps.append(step)
    return steps or list(_DEFAULT_FALLBACK_POLICY["fallback_order"])


class RoutingDecisionService:
    """Builds explainable routing metadata without owning execution orchestration."""

    def normalize_fallback_policy(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        unavailable_action = str(raw.get("unavailable_action") or _DEFAULT_FALLBACK_POLICY["unavailable_action"]).strip().lower()
        if unavailable_action not in {"mark_unavailable", "skip", "block"}:
            unavailable_action = _DEFAULT_FALLBACK_POLICY["unavailable_action"]
        return {
            "enabled": bool(raw.get("enabled", _DEFAULT_FALLBACK_POLICY["enabled"])),
            "allow_static_providers": bool(raw.get("allow_static_providers", _DEFAULT_FALLBACK_POLICY["allow_static_providers"])),
            "allow_local_backends": bool(raw.get("allow_local_backends", _DEFAULT_FALLBACK_POLICY["allow_local_backends"])),
            "allow_remote_hubs": bool(raw.get("allow_remote_hubs", _DEFAULT_FALLBACK_POLICY["allow_remote_hubs"])),
            "allow_stateful_cli": bool(raw.get("allow_stateful_cli", _DEFAULT_FALLBACK_POLICY["allow_stateful_cli"])),
            "allow_stateless_generation": bool(
                raw.get("allow_stateless_generation", _DEFAULT_FALLBACK_POLICY["allow_stateless_generation"])
            ),
            "fallback_order": _normalize_step_list(raw.get("fallback_order")),
            "unavailable_action": unavailable_action,
        }

    def resolve_fallback_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        return self.normalize_fallback_policy(
            cfg.get("routing_fallback_policy") if isinstance(cfg.get("routing_fallback_policy"), dict) else {}
        )

    def build_decision_chain(
        self,
        *,
        cfg: dict[str, Any] | None,
        task_kind: str | None,
        requested: dict[str, Any] | None,
        effective: dict[str, Any] | None,
        sources: dict[str, Any] | None,
        recommendation: dict[str, Any] | None = None,
        runtime_selection: dict[str, Any] | None = None,
        execution_backend: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fallback_policy = self.resolve_fallback_policy(cfg)
        requested = requested if isinstance(requested, dict) else {}
        effective = effective if isinstance(effective, dict) else {}
        sources = sources if isinstance(sources, dict) else {}
        steps: list[dict[str, Any]] = []

        if any(requested.get(key) for key in ("provider", "model", "base_url")):
            steps.append(
                {
                    "step": "request_override",
                    "decision": "selected",
                    "reason": "request supplied provider, model, or base_url",
                    "details": dict(requested),
                }
            )
        elif recommendation:
            steps.append(
                {
                    "step": "task_benchmark",
                    "decision": "selected",
                    "reason": str(recommendation.get("selection_source") or "benchmark recommendation"),
                    "details": dict(recommendation),
                }
            )
        else:
            steps.append(
                {
                    "step": "configured_default",
                    "decision": "selected",
                    "reason": "agent configuration default provider/model",
                    "details": {
                        "provider_source": sources.get("provider_source"),
                        "model_source": sources.get("model_source"),
                    },
                }
            )

        if runtime_selection:
            steps.append(
                {
                    "step": "local_runtime_probe",
                    "decision": "selected",
                    "reason": str(runtime_selection.get("selection_source") or "runtime probe"),
                    "details": dict(runtime_selection),
                }
            )

        if execution_backend:
            steps.append(
                {
                    "step": "execution_backend",
                    "decision": "selected",
                    "reason": str(execution_backend.get("reason") or "execution backend policy"),
                    "details": dict(execution_backend),
                }
            )

        return RoutingDecisionChain(
            policy_version="routing-decision-v1",
            task_kind=str(task_kind or "").strip() or None,
            steps=steps,
            effective=effective,
            fallback_policy=fallback_policy,
        ).as_dict()

    def provider_catalog_decision(
        self,
        *,
        cfg: dict[str, Any] | None,
        provider: dict[str, Any],
        task_kind: str | None = None,
    ) -> dict[str, Any]:
        fallback_policy = self.resolve_fallback_policy(cfg)
        capabilities = provider.get("capabilities") if isinstance(provider.get("capabilities"), dict) else {}
        provider_type = str(provider.get("provider_type") or capabilities.get("provider_type") or "static").strip()
        remote_hub = bool(provider.get("remote_hub") or capabilities.get("remote_hub"))
        dynamic_models = bool(capabilities.get("dynamic_models"))
        allowed = True
        reason = "provider_available_for_routing"
        if remote_hub and not bool(fallback_policy.get("allow_remote_hubs", True)):
            allowed = False
            reason = "remote_hub_fallback_disabled"
        elif dynamic_models and not bool(fallback_policy.get("allow_local_backends", True)) and not remote_hub:
            allowed = False
            reason = "local_backend_fallback_disabled"
        elif not dynamic_models and not bool(fallback_policy.get("allow_static_providers", True)):
            allowed = False
            reason = "static_provider_fallback_disabled"
        return {
            "policy_version": "routing-decision-v1",
            "task_kind": str(task_kind or "").strip() or None,
            "provider_type": provider_type,
            "remote_hub": remote_hub,
            "available_for_routing": allowed and bool(provider.get("available", True)),
            "reason": reason,
            "fallback_policy": fallback_policy,
        }


routing_decision_service = RoutingDecisionService()


def get_routing_decision_service() -> RoutingDecisionService:
    return routing_decision_service
