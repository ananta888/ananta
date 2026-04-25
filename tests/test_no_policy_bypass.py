from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from agent.services.deterministic_repair_path_service import (
    build_initial_repair_procedure_catalog,
    execute_repair_procedure,
)
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_service import DomainPolicyDecision


@dataclass
class _StubApprovalDecision:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class _StubApprovalService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.calls = 0

    def evaluate(self, **_kwargs) -> _StubApprovalDecision:  # noqa: ANN003
        self.calls += 1
        return _StubApprovalDecision(payload=self.payload)


def _set_task_proposal(app, tid: str, command: str) -> None:
    with app.app_context():
        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            tid,
            "proposing",
            description="policy-bypass-check",
            last_proposal={"command": command, "reason": "security regression check"},
        )


def test_normal_execution_path_is_blocked_before_shell_when_policy_blocks(client, app, admin_auth_header) -> None:
    tid = "SEC-BYPASS-NORMAL-1"
    _set_task_proposal(app, tid, "echo should-not-run")

    approval_service = _StubApprovalService(
        payload={
            "classification": "blocked",
            "reason_code": "policy_denied",
            "required_confirmation_level": "operator",
            "enforced": True,
        }
    )

    with patch("agent.services.task_execution_service.get_approval_policy_service", return_value=approval_service):
        with patch("agent.shell.PersistentShell.execute") as shell_execute:
            response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 400
    assert response.json["message"] == "tool_guardrail_blocked"
    assert approval_service.calls == 1
    assert shell_execute.call_count == 0


def test_retry_execution_path_cannot_bypass_policy_gate(client, app, admin_auth_header) -> None:
    tid = "SEC-BYPASS-RETRY-1"
    _set_task_proposal(app, tid, "echo retry-path-should-not-run")

    with app.app_context():
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["command_retries"] = 5
        cfg["command_retry_delay"] = 0
        app.config["AGENT_CONFIG"] = cfg

    approval_service = _StubApprovalService(
        payload={
            "classification": "confirm_required",
            "reason_code": "approval_required",
            "required_confirmation_level": "operator",
            "enforced": True,
        }
    )

    with patch("agent.services.task_execution_service.get_approval_policy_service", return_value=approval_service):
        with patch("agent.shell.PersistentShell.execute") as shell_execute:
            response = client.post(f"/tasks/{tid}/step/execute", json={}, headers=admin_auth_header)

    assert response.status_code == 400
    assert response.json["message"] == "tool_guardrail_blocked"
    assert approval_service.calls == 1
    assert shell_execute.call_count == 0


def test_repair_execution_path_requires_policy_approved_scope() -> None:
    catalog = build_initial_repair_procedure_catalog()
    selected = next(
        entry
        for entry in list(catalog.get("entries") or [])
        if str(((entry.get("procedure") or {}).get("safety_class") or "")).strip().lower()
        in {"review_first", "high_risk"}
    )
    procedure_id = str((selected.get("procedure") or {}).get("id") or "")
    session_id = "repair-session-new"
    target_scope = "service_runtime"
    stale_scope_key = f"{procedure_id}|{target_scope}|repair-session-old"

    result = execute_repair_procedure(
        selected_catalog_entry=selected,
        normalized_evidence={
            "schema": "deterministic_repair_evidence_v1",
            "evidence": [{"type": "log_entry", "message": "service failed to start"}],
        },
        environment_facts={"platform_target": "ubuntu"},
        dry_run=False,
        approval_policy={"approved_mutations": False, "approved_scopes": [stale_scope_key]},
        session_id=session_id,
        target_scope=target_scope,
    )

    assert result["status"] == "aborted"
    assert result["stop_reason"] == "approval_required"
    assert any(
        item.get("code") == "approval_required_for_mutation"
        for item in list(result.get("abort_conditions") or [])
    )


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


class _StubPolicyService:
    def __init__(self, decision: DomainPolicyDecision) -> None:
        self.decision = decision
        self.calls = 0

    def evaluate(self, **_kwargs) -> DomainPolicyDecision:  # noqa: ANN003
        self.calls += 1
        return self.decision


class _StubBridgeRegistry:
    def resolve(self, _domain_id: str) -> dict[str, Any]:
        return {"status": "ready", "adapter_type": "example.bridge.v1", "allowed_communication_modes": ["http"]}


def _validate_execution_path_registry(paths: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for index, path in enumerate(paths):
        path_id = str(path.get("path_id") or "").strip()
        if not path_id:
            problems.append(f"paths[{index}].path_id is required")
        gate = path.get("policy_gate")
        if not isinstance(gate, dict):
            problems.append(f"{path_id or f'paths[{index}]'} missing policy_gate metadata")
            continue
        service = str(gate.get("service") or "").strip()
        check = str(gate.get("check") or "").strip()
        if not service or not check:
            problems.append(f"{path_id or f'paths[{index}]'} policy_gate must contain service and check")
    return problems


def test_domain_action_execution_does_not_fallback_when_policy_denies() -> None:
    policy_service = _StubPolicyService(
        decision=DomainPolicyDecision(
            decision="deny",
            reason="denied_by_policy",
            domain_id="example",
            capability_id="example.script.execute",
            action_id="execute",
            details={},
        )
    )
    router = DomainActionRouter(
        domain_registry=_StubDomainRegistry(),  # type: ignore[arg-type]
        capability_registry=_StubCapabilityRegistry(),  # type: ignore[arg-type]
        policy_loader=_StubPolicyLoader(),  # type: ignore[arg-type]
        policy_service=policy_service,  # type: ignore[arg-type]
        bridge_adapter_registry=_StubBridgeRegistry(),  # type: ignore[arg-type]
    )

    result = router.route(
        domain_id="example",
        capability_id="example.script.execute",
        action_id="execute",
        execution_mode="execute",
        context_summary={"context_hash": "ctx-1"},
        actor_metadata={"role": "operator"},
        approval={
            "status": "approved",
            "approval_id": "a1",
            "domain_id": "example",
            "capability_id": "example.script.execute",
            "action_id": "execute",
        },
    ).as_dict()

    assert policy_service.calls == 1
    assert result["state"] == "denied"
    assert result["reason"] == "denied_by_policy"


def test_execution_path_registry_rejects_paths_without_policy_metadata() -> None:
    registry = [
        {
            "path_id": "normal_execution",
            "execution_kind": "task",
            "policy_gate": {"service": "approval_policy_service", "check": "evaluate"},
        },
        {
            "path_id": "new_execution_path_without_policy",
            "execution_kind": "experimental_task",
        },
    ]
    problems = _validate_execution_path_registry(registry)
    assert "new_execution_path_without_policy missing policy_gate metadata" in problems
