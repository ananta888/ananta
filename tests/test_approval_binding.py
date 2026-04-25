from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.services.deterministic_repair_path_service import (
    build_initial_repair_procedure_catalog,
    execute_repair_procedure,
)
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_service import DomainPolicyDecision


class _StubDomainRegistry:
    def get_descriptor(self, domain_id: str) -> dict[str, Any] | None:
        if domain_id != "example":
            return None
        return {"domain_id": "example", "policy_packs": [], "bridge_adapter_type": "example.bridge.v1"}

    def list_domains(self) -> list[dict[str, Any]]:
        return [{"domain_id": "example"}]


class _StubCapabilityRegistry:
    def capability(self, capability_id: str) -> dict[str, Any] | None:
        if capability_id != "example.script.execute":
            return None
        return {"capability_id": capability_id, "domain_id": "example"}


class _StubPolicyLoader:
    def load_for_domain(self, **_kwargs) -> dict[str, Any]:  # noqa: ANN003
        return {"status": "loaded", "default_decision": "default_deny", "rules": []}


@dataclass
class _StubPolicyService:
    decision: DomainPolicyDecision

    def evaluate(self, **_kwargs) -> DomainPolicyDecision:  # noqa: ANN003
        return self.decision


class _StubBridgeRegistry:
    def resolve(self, _domain_id: str) -> dict[str, Any]:
        return {"status": "ready", "adapter_type": "example.bridge.v1", "allowed_communication_modes": ["http"]}


def _router_for_approval_required() -> DomainActionRouter:
    return DomainActionRouter(
        domain_registry=_StubDomainRegistry(),  # type: ignore[arg-type]
        capability_registry=_StubCapabilityRegistry(),  # type: ignore[arg-type]
        policy_loader=_StubPolicyLoader(),  # type: ignore[arg-type]
        policy_service=_StubPolicyService(
            decision=DomainPolicyDecision(
                decision="approval_required",
                reason="mutation_requires_approval",
                domain_id="example",
                capability_id="example.script.execute",
                action_id="execute",
                details={},
            )
        ),  # type: ignore[arg-type]
        bridge_adapter_registry=_StubBridgeRegistry(),  # type: ignore[arg-type]
    )


def _route_with_approval(approval: dict[str, Any] | None) -> dict[str, Any]:
    return _router_for_approval_required().route(
        domain_id="example",
        capability_id="example.script.execute",
        action_id="execute",
        execution_mode="execute",
        context_summary={"context_hash": "ctx-123"},
        actor_metadata={"role": "operator"},
        approval=approval,
    ).as_dict()


def test_approval_binding_rejects_missing_malformed_or_wrong_action() -> None:
    missing = _route_with_approval(None)
    malformed = _route_with_approval({"approval_confirmed": True})
    wrong_action = _route_with_approval(
        {
            "status": "approved",
            "approval_id": "apr-1",
            "domain_id": "example",
            "capability_id": "example.script.execute",
            "action_id": "delete",
            "context_hash": "ctx-123",
        }
    )

    assert missing["state"] == "approval_required"
    assert missing["reason"] == "missing_approval"
    assert malformed["state"] == "approval_required"
    assert malformed["reason"] in {"approval_not_granted", "approval_reference_missing"}
    assert wrong_action["state"] == "approval_required"
    assert wrong_action["reason"] == "approval_action_mismatch"


def test_approval_binding_rejects_stale_and_context_mismatched_approvals() -> None:
    stale_approval = {
        "status": "approved",
        "approval_id": "apr-stale",
        "domain_id": "example",
        "capability_id": "example.script.execute",
        "action_id": "execute",
        "context_hash": "ctx-123",
        "expires_at": (datetime.now(tz=timezone.utc) - timedelta(minutes=5)).isoformat(),
    }
    wrong_context = {
        "status": "approved",
        "approval_id": "apr-context",
        "domain_id": "example",
        "capability_id": "example.script.execute",
        "action_id": "execute",
        "context_hash": "ctx-other",
        "expires_at": (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).isoformat(),
    }

    stale = _route_with_approval(stale_approval)
    mismatched_context = _route_with_approval(wrong_context)

    assert stale["state"] == "approval_required"
    assert stale["reason"] == "approval_stale"
    assert mismatched_context["state"] == "approval_required"
    assert mismatched_context["reason"] == "approval_context_mismatch"


def test_approval_binding_allows_only_correct_action_capability_and_context_hash() -> None:
    approved = _route_with_approval(
        {
            "status": "approved",
            "approval_id": "apr-valid",
            "domain_id": "example",
            "capability_id": "example.script.execute",
            "action_id": "execute",
            "context_hash": "ctx-123",
            "expires_at": (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat(),
        }
    )
    assert approved["state"] == "execution_started"
    assert approved["reason"] == "policy_passed_execution_started"


def test_repair_path_requires_fresh_scope_bound_approval_when_policy_demands_it() -> None:
    catalog = build_initial_repair_procedure_catalog()
    selected = next(
        entry
        for entry in list(catalog.get("entries") or [])
        if str(((entry.get("procedure") or {}).get("safety_class") or "")).strip().lower()
        in {"review_first", "high_risk"}
    )
    procedure_id = str((selected.get("procedure") or {}).get("id") or "")
    target_scope = "service_runtime"
    session_old = "repair-session-old"
    session_new = "repair-session-new"
    scope_old = f"{procedure_id}|{target_scope}|{session_old}"
    scope_new = f"{procedure_id}|{target_scope}|{session_new}"

    stale_scope_result = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence={
            "schema": "deterministic_repair_evidence_v1",
            "evidence": [{"type": "log_entry", "message": "runtime issue"}],
        },
        environment_facts={"platform_target": "ubuntu"},
        dry_run=False,
        approval_policy={"approved_mutations": False, "approved_scopes": [scope_old]},
        session_id=session_new,
        target_scope=target_scope,
    )
    fresh_scope_result = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence={
            "schema": "deterministic_repair_evidence_v1",
            "evidence": [{"type": "log_entry", "message": "health probe stable"}],
        },
        environment_facts={"platform_target": "ubuntu"},
        dry_run=False,
        approval_policy={"approved_mutations": False, "approved_scopes": [scope_new]},
        session_id=session_new,
        target_scope=target_scope,
    )

    assert stale_scope_result["stop_reason"] == "approval_required"
    assert any(
        item.get("code") == "approval_required_for_mutation"
        for item in list(stale_scope_result.get("abort_conditions") or [])
    )
    assert fresh_scope_result["stop_reason"] != "approval_required"
