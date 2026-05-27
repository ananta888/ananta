from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DelegationRequest:
    owner_role: str
    delegate_role: str
    goal_id: str
    task_id: str


@dataclass(frozen=True)
class HubDecision:
    allowed: bool
    reason_code: str
    outcome: str

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


class AegisHub:
    def __init__(self, *, orchestrator_role: str = "hub") -> None:
        self._orchestrator_role = orchestrator_role

    def evaluate_delegation(self, request: DelegationRequest) -> HubDecision:
        owner = str(request.owner_role or "").strip().lower()
        delegate = str(request.delegate_role or "").strip().lower()
        if self._orchestrator_role != "hub":
            return HubDecision(False, "hub_orchestrator_required", "blocked")
        if owner != "hub":
            return HubDecision(False, "delegation_owner_must_be_hub", "blocked")
        if owner.startswith("worker") and delegate.startswith("worker"):
            return HubDecision(False, "worker_to_worker_forbidden", "blocked")
        return HubDecision(True, "delegation_allowed", "delegated")

    def decide_task_outcome(self, *, flow_state: str, verification_ok: bool, artifact_verified: bool) -> HubDecision:
        normalized_state = str(flow_state or "").strip().lower()
        if normalized_state == "rollback":
            return HubDecision(True, "rollback_required", "rollback")
        if normalized_state in {"retry", "blocked"} or not verification_ok:
            return HubDecision(True, "verification_retry_required", "retry")
        if normalized_state == "artifact" and artifact_verified:
            return HubDecision(True, "artifact_verified", "approve")
        return HubDecision(False, "approval_preconditions_missing", "blocked")
