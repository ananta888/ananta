from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_service import DomainPolicyDecision


class _StubDomainRegistry:
    def __init__(self, descriptor: dict[str, Any]) -> None:
        self._descriptor = dict(descriptor)

    def get_descriptor(self, domain_id: str) -> dict[str, Any] | None:
        if str(domain_id).strip() != str(self._descriptor.get("domain_id") or "").strip():
            return None
        return dict(self._descriptor)

    def list_domains(self) -> list[dict[str, Any]]:
        return [{"domain_id": self._descriptor["domain_id"]}]


class _StubCapabilityRegistry:
    def __init__(self, capability_payload: dict[str, Any]) -> None:
        self._capability_payload = dict(capability_payload)

    def capability(self, capability_id: str) -> dict[str, Any] | None:
        if str(capability_id).strip() != str(self._capability_payload.get("capability_id") or "").strip():
            return None
        return dict(self._capability_payload)


class _StubPolicyLoader:
    def __init__(self, policy_document: dict[str, Any]) -> None:
        self.policy_document = dict(policy_document)

    def load_for_domain(self, *, domain_id: str, policy_refs: list[str], known_domains: set[str]) -> dict[str, Any]:
        del domain_id, policy_refs, known_domains
        return dict(self.policy_document)


@dataclass
class _CountingPolicyService:
    decision: DomainPolicyDecision
    calls: int = 0

    def evaluate(
        self,
        *,
        domain_id: str,
        capability_id: str,
        action_id: str,
        context_summary: dict[str, Any] | None,
        actor_metadata: dict[str, Any] | None,
        policy_document: dict[str, Any] | None,
    ) -> DomainPolicyDecision:
        del domain_id, capability_id, action_id, context_summary, actor_metadata, policy_document
        self.calls += 1
        return self.decision


class _StubBridgeRegistry:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = dict(state)

    def resolve(self, domain_id: str) -> dict[str, Any]:
        del domain_id
        return dict(self.state)


def _build_router(
    *,
    decision: DomainPolicyDecision,
    bridge_state: dict[str, Any] | None = None,
) -> tuple[DomainActionRouter, _CountingPolicyService]:
    descriptor = {"domain_id": "example", "policy_packs": [], "bridge_adapter_type": "example.bridge.v1"}
    capability = {"capability_id": "example.read.status", "domain_id": "example"}
    policy_service = _CountingPolicyService(decision=decision)
    router = DomainActionRouter(
        domain_registry=_StubDomainRegistry(descriptor),  # type: ignore[arg-type]
        capability_registry=_StubCapabilityRegistry(capability),  # type: ignore[arg-type]
        policy_loader=_StubPolicyLoader({"status": "loaded", "default_decision": "default_deny", "rules": []}),  # type: ignore[arg-type]
        policy_service=policy_service,  # type: ignore[arg-type]
        bridge_adapter_registry=_StubBridgeRegistry(
            bridge_state
            or {"status": "ready", "adapter_type": "example.bridge.v1", "allowed_communication_modes": ["http"]}
        ),  # type: ignore[arg-type]
    )
    return router, policy_service


def test_domain_action_router_returns_plan_and_enforces_policy_evaluation() -> None:
    decision = DomainPolicyDecision(
        decision="allow",
        reason="read_allowed",
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        details={},
    )
    router, policy_service = _build_router(decision=decision)

    result = router.route(
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        execution_mode="plan",
        context_summary={"origin": "test"},
        actor_metadata={"role": "user"},
    ).as_dict()

    assert result["state"] == "plan"
    assert policy_service.calls == 1


def test_domain_action_router_returns_approval_required_for_missing_approval() -> None:
    decision = DomainPolicyDecision(
        decision="approval_required",
        reason="mutation_requires_approval",
        domain_id="example",
        capability_id="example.read.status",
        action_id="write",
        details={},
    )
    router, _policy_service = _build_router(decision=decision)

    result = router.route(
        domain_id="example",
        capability_id="example.read.status",
        action_id="write",
        execution_mode="execute",
        context_summary={},
        actor_metadata={},
        approval=None,
    ).as_dict()

    assert result["state"] == "approval_required"
    assert result["reason"] == "missing_approval"


def test_domain_action_router_starts_execution_when_approval_matches_action() -> None:
    decision = DomainPolicyDecision(
        decision="approval_required",
        reason="mutation_requires_approval",
        domain_id="example",
        capability_id="example.read.status",
        action_id="write",
        details={},
    )
    router, _policy_service = _build_router(decision=decision)

    result = router.route(
        domain_id="example",
        capability_id="example.read.status",
        action_id="write",
        execution_mode="execute",
        context_summary={},
        actor_metadata={},
        approval={
            "status": "approved",
            "domain_id": "example",
            "capability_id": "example.read.status",
            "action_id": "write",
            "approval_id": "apr-123",
        },
    ).as_dict()

    assert result["state"] == "execution_started"


def test_domain_action_router_denies_when_policy_denies_action() -> None:
    decision = DomainPolicyDecision(
        decision="deny",
        reason="denied_by_policy",
        domain_id="example",
        capability_id="example.read.status",
        action_id="delete",
        details={},
    )
    router, _policy_service = _build_router(decision=decision)

    result = router.route(
        domain_id="example",
        capability_id="example.read.status",
        action_id="delete",
        execution_mode="execute",
        context_summary={},
        actor_metadata={},
    ).as_dict()

    assert result["state"] == "denied"
    assert result["reason"] == "denied_by_policy"


def test_domain_action_router_returns_degraded_when_bridge_unavailable() -> None:
    decision = DomainPolicyDecision(
        decision="allow",
        reason="allowed",
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        details={},
    )
    router, _policy_service = _build_router(
        decision=decision,
        bridge_state={"status": "degraded", "reason": "adapter_disabled"},
    )

    result = router.route(
        domain_id="example",
        capability_id="example.read.status",
        action_id="read",
        execution_mode="execute",
        context_summary={},
        actor_metadata={},
    ).as_dict()

    assert result["state"] == "degraded"
    assert result["reason"] == "bridge_unavailable"

