from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from agent.services.context_bundle_service import get_context_bundle_service
from agent.services.repository_registry import get_repository_registry

# OHA-013: sensitivity levels that are denied for cloud/external destinations by default
_CLOUD_DENY_SENSITIVITIES = {"internal_high", "secret", "credential", "security_sensitive"}
_SENSITIVITY_ORDER = ["public", "internal", "internal_high", "secret", "credential", "security_sensitive"]


def _intent_to_tree_scope(retrieval_intent: str) -> str:
    """Map retrieval intent to the most appropriate MemoryTree scope."""
    intent = str(retrieval_intent or "").lower()
    if any(k in intent for k in ("architecture", "decision", "cross_module", "global")):
        return "global"
    if any(k in intent for k in ("topic", "symbol", "module", "domain")):
        return "topic"
    if any(k in intent for k in ("config", "integration", "contract")):
        return "topic"
    return "source"  # default: most specific / cheapest


def _build_destination(
    *,
    worker_id: str = "hub",
    runtime_kind: str = "local",
    provider_location: str = "local",
    model_scope: str = "local_only",
    cloud_effective: bool = False,
    external_effective: bool = False,
) -> dict[str, Any]:
    return {
        "worker_id": worker_id,
        "runtime_kind": runtime_kind,
        "provider_location": provider_location,
        "model_scope": model_scope,
        "cloud_effective": cloud_effective,
        "external_effective": external_effective,
        "local_effective": not cloud_effective and not external_effective,
    }


def filter_chunks_for_destination(
    chunks: list[dict],
    destination: dict[str, Any],
    *,
    sensitivity_ceiling: str = "internal_high",
) -> dict[str, Any]:
    """Apply destination-aware sensitivity filter to a list of chunk dicts.

    Returns a dict with allowed/denied counts and denied_reasons.
    """
    cloud_or_external = destination.get("cloud_effective") or destination.get("external_effective")
    allowed: list[dict] = []
    denied_count = 0
    denied_reasons: list[str] = []

    ceiling_idx = _SENSITIVITY_ORDER.index(sensitivity_ceiling) if sensitivity_ceiling in _SENSITIVITY_ORDER else 2

    for chunk in chunks:
        meta = dict(chunk.get("metadata") or {})
        sens = str(meta.get("sensitivity") or "public").lower()
        sens_idx = _SENSITIVITY_ORDER.index(sens) if sens in _SENSITIVITY_ORDER else 1

        reason: str | None = None
        if cloud_or_external and sens in _CLOUD_DENY_SENSITIVITIES:
            reason = f"cloud_deny:{sens}"
        elif sens_idx > ceiling_idx:
            reason = f"ceiling_exceeded:{sens}>{sensitivity_ceiling}"

        if reason:
            denied_count += 1
            if reason not in denied_reasons:
                denied_reasons.append(reason)
        else:
            allowed.append(chunk)

    return {
        "allowed_chunks": allowed,
        "allowed_count": len(allowed),
        "denied_count": denied_count,
        "denied_reasons": denied_reasons,
        "input_count": len(chunks),
    }


class TaskContextPolicyService:
    """Leitet Context-Bundle-Policy, Retrieval-Hints und Task-Nachbarschaft für Delegation ab."""

    def resolve_context_bundle_policy(self) -> dict[str, Any]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
        return get_context_bundle_service().resolve_context_bundle_policy(
            (agent_cfg or {}).get("context_bundle_policy")
        )

    def default_retrieval_hints_for_task_kind(self, task_kind: str | None) -> dict[str, str]:
        normalized = str(task_kind or "").strip().lower()
        if normalized in {"bugfix", "testing", "test"}:
            return {
                "retrieval_intent": "localize_failure_and_fix",
                "required_context_scope": "local_code_and_failure_neighbors",
                "preferred_bundle_mode": "standard",
            }
        if normalized in {"refactor", "implement", "coding"}:
            return {
                "retrieval_intent": "symbol_and_dependency_neighborhood",
                "required_context_scope": "module_and_related_symbols",
                "preferred_bundle_mode": "standard",
            }
        if normalized in {"architecture", "analysis", "doc", "research"}:
            return {
                "retrieval_intent": "architecture_and_decision_context",
                "required_context_scope": "cross_module_docs_and_contracts",
                "preferred_bundle_mode": "full",
            }
        if normalized in {"config", "xml", "ops"}:
            return {
                "retrieval_intent": "configuration_contracts_and_runtime_edges",
                "required_context_scope": "config_and_integration_points",
                "preferred_bundle_mode": "standard",
            }
        return {
            "retrieval_intent": "execution_focused_context",
            "required_context_scope": "task_and_direct_neighbors",
            "preferred_bundle_mode": "standard",
        }

    def derive_retrieval_hints(
        self,
        *,
        parent_task: dict[str, Any],
        data: Any,
        effective_task_kind: str | None,
    ) -> dict[str, str]:
        defaults = self.default_retrieval_hints_for_task_kind(effective_task_kind)
        retrieval_intent = (
            str(getattr(data, "retrieval_intent", None) or "").strip()
            or str(parent_task.get("retrieval_intent") or "").strip()
            or defaults["retrieval_intent"]
        )
        required_context_scope = (
            str(getattr(data, "required_context_scope", None) or "").strip()
            or str(parent_task.get("required_context_scope") or "").strip()
            or defaults["required_context_scope"]
        )
        preferred_bundle_mode = (
            str(getattr(data, "preferred_bundle_mode", None) or "").strip().lower()
            or str(parent_task.get("preferred_bundle_mode") or "").strip().lower()
            or defaults["preferred_bundle_mode"]
        )
        if preferred_bundle_mode not in {"compact", "standard", "full"}:
            preferred_bundle_mode = defaults["preferred_bundle_mode"]
        return {
            "retrieval_intent": retrieval_intent,
            "required_context_scope": required_context_scope,
            "preferred_bundle_mode": preferred_bundle_mode,
        }

    def derive_task_neighborhood(self, *, parent_task: dict[str, Any]) -> dict[str, list[str]]:
        parent_task_id = str(parent_task.get("id") or "").strip()
        goal_id = str(parent_task.get("goal_id") or "").strip()
        parent_parent_id = str(parent_task.get("parent_task_id") or "").strip()
        depends_on = [str(item).strip() for item in list(parent_task.get("depends_on") or []) if str(item).strip()]

        repos = get_repository_registry()
        task_rows = repos.task_repo.get_all()
        sibling_ids: list[str] = []
        completed_neighbor_ids: list[str] = []
        dependent_task_ids: list[str] = []
        for row in task_rows:
            item = row.model_dump()
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id == parent_task_id:
                continue
            item_depends_on = [str(dep).strip() for dep in list(item.get("depends_on") or []) if str(dep).strip()]
            if parent_task_id and parent_task_id in item_depends_on:
                dependent_task_ids.append(item_id)
            if parent_parent_id and str(item.get("parent_task_id") or "").strip() == parent_parent_id:
                sibling_ids.append(item_id)
            if goal_id and str(item.get("goal_id") or "").strip() == goal_id:
                status = str(item.get("status") or "").strip().lower()
                if status in {"completed", "failed"}:
                    completed_neighbor_ids.append(item_id)

        ordered_neighbors: list[str] = []
        for task_id in [*depends_on, *dependent_task_ids, *sibling_ids, *completed_neighbor_ids]:
            if task_id and task_id != parent_task_id and task_id not in ordered_neighbors:
                ordered_neighbors.append(task_id)
            if len(ordered_neighbors) >= 12:
                break
        return {
            "depends_on_task_ids": depends_on,
            "dependent_task_ids": dependent_task_ids[:12],
            "sibling_task_ids": sibling_ids[:12],
            "completed_neighbor_task_ids": completed_neighbor_ids[:12],
            "neighbor_task_ids": ordered_neighbors[:12],
        }

    def build_context_policy(
        self,
        *,
        parent_task: dict[str, Any],
        data: Any,
        effective_task_kind: str | None,
        # OHA-013: destination override (defaults to local hub worker)
        destination: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, list[str]]]:
        """Kombiniere Bundle-Policy, Retrieval-Hints und Task-Nachbarschaft zu einer Context-Policy."""

        retrieval_hints = self.derive_retrieval_hints(
            parent_task=parent_task,
            data=data,
            effective_task_kind=effective_task_kind,
        )
        task_neighborhood = self.derive_task_neighborhood(parent_task=parent_task)

        # OHA-013: MemoryTree scope and destination
        tree_scope = _intent_to_tree_scope(retrieval_hints["retrieval_intent"])
        resolved_destination = destination or _build_destination()

        context_policy = {
            **self.resolve_context_bundle_policy(),
            "task_kind": effective_task_kind,
            "retrieval_intent": retrieval_hints["retrieval_intent"],
            "required_context_scope": retrieval_hints["required_context_scope"],
            "preferred_bundle_mode": retrieval_hints["preferred_bundle_mode"],
            # OHA-013: new fields
            "retrieval_tree_scope": tree_scope,
            "destination": resolved_destination,
            **task_neighborhood,
        }
        return context_policy, retrieval_hints, task_neighborhood


_task_context_policy_service = TaskContextPolicyService()


def get_task_context_policy_service() -> TaskContextPolicyService:
    return _task_context_policy_service
