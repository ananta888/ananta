import logging
from typing import Any

from agent.services.verification_policy_service import default_verification_spec
from agent.services.worker_routing_policy_utils import (
    derive_required_capabilities,
    extract_blueprint_role_defaults,
)

logger = logging.getLogger(__name__)


def infer_subtask_task_kind(subtask: dict[str, Any]) -> str:
    task_like = {
        "title": str(subtask.get("title") or ""),
        "description": str(subtask.get("description") or ""),
    }
    capabilities = derive_required_capabilities(task_like)
    for kind in ("testing", "review", "planning", "research", "coding"):
        if kind in capabilities:
            return kind
    return "coding"


def retrieval_hints_for_task_kind(task_kind: str | None) -> dict[str, str]:
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


def sanitize_blueprint_provenance(subtask: dict[str, Any]) -> dict[str, str]:
    role_hints = list(subtask.get("blueprint_role_template_hints") or [])
    primary_hint = role_hints[0] if role_hints and isinstance(role_hints[0], dict) else {}
    blueprint_role_name = (
        str(subtask.get("blueprint_role_name") or "").strip()
        or str(primary_hint.get("role_name") or "").strip()
    )
    template_name = (
        str(subtask.get("template_name") or "").strip()
        or str(primary_hint.get("template_name") or "").strip()
        or str(primary_hint.get("template_id") or "").strip()
    )
    provenance = {
        "blueprint_id": str(subtask.get("blueprint_id") or "").strip(),
        "blueprint_name": str(subtask.get("blueprint_name") or "").strip(),
        "blueprint_artifact_id": str(subtask.get("blueprint_artifact_id") or "").strip(),
        "blueprint_role_name": blueprint_role_name,
        "template_name": template_name,
        "template_id": str(subtask.get("template_id") or "").strip(),
    }
    return {key: value for key, value in provenance.items() if value}


def sanitize_role_defaults(subtask: dict[str, Any]) -> dict[str, Any]:
    explicit_defaults = extract_blueprint_role_defaults(subtask)
    if explicit_defaults:
        return explicit_defaults

    role_hints = list(subtask.get("blueprint_role_template_hints") or [])
    if not role_hints or not isinstance(role_hints[0], dict):
        return {}
    hint = dict(role_hints[0])
    return extract_blueprint_role_defaults(
        {
            "blueprint_role_defaults": {
                "capability_defaults": hint.get("capability_defaults"),
                "risk_profile": hint.get("risk_profile"),
                "verification_defaults": hint.get("verification_defaults"),
            }
        }
    )


_ALLOWED_CAPS_BY_KIND: dict[str, set[str]] = {
    "coding": {"coding", "analysis", "doc"},
    "testing": {"testing", "analysis", "doc"},
    "review": {"review", "analysis", "doc"},
    "research": {"research", "analysis", "doc"},
    "planning": {"planning", "analysis", "doc"},
    "ops": {"ops", "analysis", "doc"},
    "analysis": {"analysis", "doc"},
    "doc": {"doc", "analysis"},
}


def sanitize_llm_subtask_policy_hints(subtask: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    out = dict(subtask or {})
    warnings: list[str] = []
    task_kind = str(out.get("task_kind") or "").strip().lower() or infer_subtask_task_kind(out)
    allowed_caps = _ALLOWED_CAPS_BY_KIND.get(task_kind, {"analysis", "doc"})
    requested_caps = [str(item).strip().lower() for item in list(out.get("required_capabilities") or []) if str(item).strip()]
    filtered_caps = [cap for cap in requested_caps if cap in allowed_caps]
    if requested_caps and len(filtered_caps) != len(requested_caps):
        warnings.append("capability_escalation_blocked")
    out["required_capabilities"] = filtered_caps

    requested_scope = str(out.get("context_scope") or "").strip().lower()
    if requested_scope in {"full", "global", "admin"}:
        warnings.append("context_scope_escalation_blocked")
        out.pop("context_scope", None)

    if "tool_permissions" in out or "allowed_tools" in out:
        warnings.append("tool_escalation_blocked")
        out.pop("tool_permissions", None)
        out.pop("allowed_tools", None)
    return out, warnings


def merge_verification_defaults(
    base_verification_spec: dict[str, Any],
    role_defaults: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base_verification_spec or {})
    if not role_defaults:
        return merged

    merged["blueprint_role_defaults"] = dict(role_defaults)
    verification_defaults = role_defaults.get("verification_defaults")
    if not isinstance(verification_defaults, dict):
        return merged

    if bool(verification_defaults.get("required")):
        merged["required"] = True
    if bool(verification_defaults.get("policy")):
        merged["policy"] = True

    gates: list[str] = []
    for item in list(verification_defaults.get("gates") or []):
        gate = str(item).strip()
        if gate and gate not in gates:
            gates.append(gate)
    if gates:
        existing_gates = [
            str(item).strip()
            for item in list(merged.get("required_gates") or [])
            if str(item).strip()
        ]
        for gate in gates:
            if gate not in existing_gates:
                existing_gates.append(gate)
        merged["required_gates"] = existing_gates
    return merged
