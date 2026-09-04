"""Hub-side admission for agent intents and fenced shared-resource leases."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent.services.collaboration_budget_service import CollaborationBudgetService
from agent.services.collaboration_workspace_policy import CollaborationWorkspacePolicy
from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_resources import AgentIntentV1, SharedResourceOfferV1
from ananta_contracts.collaboration_workspace import canonical_digest, require_id


class CollaborationAssignmentAuthority(Protocol):
    """Narrow Hub task-system port; implementations own routing and worker choice."""

    def decide(self, *, tenant_id: str, intent: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CollaborationAgentControlService:
    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        policy: CollaborationWorkspacePolicy,
        assignment_authority: CollaborationAssignmentAuthority,
        clock: Callable[[], float] = time.time,
        maximum_correlation_intents: int = 16,
        budget: CollaborationBudgetService | None = None,
    ) -> None:
        if not 1 <= maximum_correlation_intents <= 100:
            raise ValueError("collaboration_agent_loop_limit_invalid")
        self._store = store
        self._policy = policy
        self._authority = assignment_authority
        self._clock = clock
        self._maximum_correlation_intents = maximum_correlation_intents
        self._budget = budget

    def publish_offer(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        offer: Mapping[str, Any],
    ) -> dict[str, Any]:
        parsed = SharedResourceOfferV1.from_mapping(offer)
        self._authorize(tenant_id, parsed.workspace_id, principal_actor_id, "event.write")
        if parsed.owner_actor_binding_id != principal_actor_id:
            raise PermissionError("collaboration_resource_offer_owner_mismatch")
        if parsed.expires_at <= self._clock():
            raise ValueError("collaboration_resource_offer_expired")
        value, replayed = self._store.put_resource_offer(tenant_id, parsed.to_dict())
        return {**value, "replayed": replayed, "lease_granted": False}

    def list_offers(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        principal_actor_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.read")
        offers = self._store.resource_offers(tenant_id, workspace_id, now=self._clock(), limit=limit)
        return {
            "items": [
                offer
                for offer in offers
                if offer["sensitivity"] == "workspace" or offer["owner_actor_binding_id"] == principal_actor_id
            ],
            "limit": limit,
        }

    def propose_intent(
        self,
        *,
        tenant_id: str,
        principal_actor_id: str,
        intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        parsed = AgentIntentV1.from_mapping(intent)
        self._authorize(tenant_id, parsed.workspace_id, principal_actor_id, "event.write")
        if parsed.actor_binding_id != principal_actor_id:
            raise PermissionError("collaboration_agent_intent_actor_mismatch")
        if not self._store.room_visible(tenant_id, parsed.workspace_id, parsed.room_id, principal_actor_id):
            raise PermissionError("collaboration_room_visibility_denied")
        if parsed.target_actor_binding_id is not None:
            target_membership = self._store.membership(tenant_id, parsed.workspace_id, parsed.target_actor_binding_id)
            target_actor = self._store.actor(tenant_id, parsed.target_actor_binding_id)
            if not target_membership or target_membership.get("status") != "active" or not target_actor:
                raise PermissionError("collaboration_agent_intent_target_unavailable")
            if parsed.intent_type in {"mention", "handoff_request", "propose_task"} and target_actor[
                "actor_kind"
            ] not in {"agent", "service"}:
                raise PermissionError("collaboration_agent_intent_target_kind_invalid")
        if self._budget is not None:
            target = (
                self._store.actor(tenant_id, parsed.target_actor_binding_id)
                if parsed.target_actor_binding_id is not None
                else None
            )
            profile = target.get("profile") if isinstance(target, Mapping) else None
            provider = profile.get("provider") if isinstance(profile, Mapping) else None
            budget = self._budget.admit(
                tenant_id=tenant_id,
                workspace_id=parsed.workspace_id,
                traffic_class="agent_intent",
                dimensions={
                    "room": parsed.room_id,
                    "principal": principal_actor_id,
                    "actor": parsed.target_actor_binding_id or parsed.actor_binding_id,
                    "task": parsed.task_id,
                    "provider": str(provider or "").strip() or None,
                    "intent_chain": parsed.correlation_id,
                    "connection": "hub-agent-control",
                },
            )
            if not budget["allowed"]:
                raise PermissionError(budget["reason_code"])
        admitted, replayed = self._store.admit_agent_intent(
            tenant_id,
            parsed.to_dict(),
            maximum_correlation_intents=self._maximum_correlation_intents,
        )
        if replayed or parsed.intent_type not in {"propose_task", "handoff_request"}:
            if not replayed:
                admitted = self._store.decide_agent_intent(
                    tenant_id,
                    parsed.workspace_id,
                    parsed.intent_id,
                    state="accepted",
                    reason_code="intent_recorded",
                    assignment=None,
                )
            return {**admitted, "replayed": replayed, "worker_invoked": False}
        decision = self._validate_assignment_decision(
            self._authority.decide(tenant_id=tenant_id, intent=parsed.to_dict())
        )
        state = "accepted" if decision["authorized"] else "denied"
        decided = self._store.decide_agent_intent(
            tenant_id,
            parsed.workspace_id,
            parsed.intent_id,
            state=state,
            reason_code=decision["reason_code"],
            assignment=decision["assignment"] if decision["authorized"] else None,
        )
        return {**decided, "replayed": False, "worker_invoked": False}

    def reserve_resource_lease(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        offer_id: str,
        assignment: Mapping[str, Any],
    ) -> dict[str, Any]:
        offer = self._store.resource_offer(tenant_id, workspace_id, offer_id)
        if offer is None:
            raise KeyError("collaboration_resource_offer_not_found")
        now = self._clock()
        if offer["status"] != "active" or float(offer["expires_at"]) <= now:
            raise PermissionError("collaboration_resource_offer_unavailable")
        if offer["attestation_status"] != "verified":
            raise PermissionError("collaboration_resource_attestation_required")
        decision = self._validate_assignment_decision(
            {"authorized": True, "reason_code": "lease", "assignment": assignment}
        )
        selected = decision["assignment"]
        operations = selected["allowed_operations"]
        if not set(operations).issubset(offer["scopes"]):
            raise PermissionError("collaboration_resource_scope_exceeded")
        lease_id = f"lease-{
            canonical_digest(
                {
                    'workspace_id': workspace_id,
                    'offer_id': offer_id,
                    'task_id': selected['task_id'],
                    'assignment_id': selected['assignment_id'],
                }
            )[:32]
        }"
        existing = self._store.resource_lease(tenant_id, workspace_id, lease_id)
        if existing is not None:
            return {**existing, "replayed": True}
        lease = {
            "schema": "ananta.collaboration-resource-lease.v1",
            "lease_id": lease_id,
            "workspace_id": require_id(workspace_id, "workspace_id"),
            "resource_id": offer["resource_id"],
            "task_id": selected["task_id"],
            "assignment_id": selected["assignment_id"],
            "worker_id": selected["worker_id"],
            "allowed_operations": operations,
            "budget_units": selected["budget_units"],
            "fencing_token": int(now * 1_000_000),
            "issued_at": now,
            "expires_at": min(float(offer["expires_at"]), now + selected["duration_seconds"]),
            "status": "active",
        }
        value, replayed = self._store.reserve_resource_lease(tenant_id, lease)
        return {**value, "replayed": replayed}

    def admit_resource_result(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        task_id: str,
        assignment_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        lease = self._store.validate_resource_result(
            tenant_id,
            workspace_id,
            lease_id,
            task_id=task_id,
            assignment_id=assignment_id,
            fencing_token=fencing_token,
            now=self._clock(),
        )
        return {"accepted": True, "lease": lease, "reason_code": "resource_result_admitted"}

    def cancel_task(
        self, *, tenant_id: str, workspace_id: str, principal_actor_id: str, task_id: str
    ) -> dict[str, Any]:
        self._authorize(tenant_id, workspace_id, principal_actor_id, "event.write")
        revoked = self._store.revoke_resource_leases(tenant_id, workspace_id, task_id=task_id)
        return {"task_id": task_id, "revoked_leases": revoked, "reason_code": "task_leases_revoked"}

    def _authorize(self, tenant_id: str, workspace_id: str, actor_id: str, capability: str) -> None:
        self._policy.require(self._store.membership(tenant_id, workspace_id, actor_id), capability)

    @staticmethod
    def _validate_assignment_decision(value: Mapping[str, Any]) -> dict[str, Any]:
        if set(value) != {"authorized", "reason_code", "assignment"} or not isinstance(value["authorized"], bool):
            raise ValueError("collaboration_assignment_decision_invalid")
        reason = require_id(value["reason_code"], "assignment_reason_code")
        assignment = value["assignment"]
        if not value["authorized"]:
            if assignment not in ({}, None):
                raise ValueError("collaboration_assignment_denial_invalid")
            return {"authorized": False, "reason_code": reason, "assignment": {}}
        if not isinstance(assignment, Mapping) or set(assignment) != {
            "task_id",
            "assignment_id",
            "worker_id",
            "allowed_operations",
            "budget_units",
            "duration_seconds",
        }:
            raise ValueError("collaboration_assignment_projection_invalid")
        operations = assignment["allowed_operations"]
        if (
            not isinstance(operations, list)
            or not operations
            or len(operations) > 32
            or len(set(operations)) != len(operations)
            or not isinstance(assignment["budget_units"], int)
            or isinstance(assignment["budget_units"], bool)
            or not 1 <= assignment["budget_units"] <= 1_000_000
            or not isinstance(assignment["duration_seconds"], (int, float))
            or isinstance(assignment["duration_seconds"], bool)
            or not 1 <= assignment["duration_seconds"] <= 86_400
        ):
            raise ValueError("collaboration_assignment_limits_invalid")
        normalized = {
            **dict(assignment),
            "task_id": require_id(assignment["task_id"], "task_id"),
            "assignment_id": require_id(assignment["assignment_id"], "assignment_id"),
            "worker_id": require_id(assignment["worker_id"], "worker_id"),
            "allowed_operations": [require_id(item, "allowed_operation") for item in operations],
            "duration_seconds": float(assignment["duration_seconds"]),
        }
        return {"authorized": True, "reason_code": reason, "assignment": normalized}


__all__ = ["CollaborationAgentControlService", "CollaborationAssignmentAuthority"]
