from __future__ import annotations

from agent.routes.tasks.orchestration_policy import compute_lease_expiry, extract_active_lease, persist_policy_decision
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.knowledge_index_task_ingress_policy import (
    bound_knowledge_index_mutation_error,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.vector_task_admin_guard_service import (
    generic_vector_mutation_error,
)


class TaskClaimService:
    """Hub-owned claim and orchestration read-model use-cases."""

    def claim_task(
        self,
        *,
        task_id: str,
        agent_url: str,
        requested_lease: int,
        idempotency_key: str,
        policy,
        task_queue_service,
    ) -> dict:
        repos = get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if not task:
            return {"error": "not_found", "code": 404}
        bound_conflict = bound_knowledge_index_mutation_error(
            task,
            action="claim",
        )
        if bound_conflict:
            return bound_conflict
        vector_error = generic_vector_mutation_error(task)
        if vector_error is not None:
            return vector_error

        lease_seconds = policy.validate_lease_duration(requested_lease)
        lease_until = compute_lease_expiry(lease_seconds)
        claim_decision: dict[str, object] = {}

        def validate_authoritative(
            task_payload: dict,
        ) -> tuple[bool, str | None]:
            bound_denial = bound_knowledge_index_mutation_error(
                task_payload,
                action="claim",
            )
            if bound_denial is not None:
                claim_decision["knowledge_index_denial"] = (
                    bound_denial
                )
                return False, str(bound_denial["error"])
            vector_denial = generic_vector_mutation_error(
                task_payload
            )
            if vector_denial is not None:
                claim_decision["vector_denial"] = vector_denial
                return False, str(vector_denial["error"])
            can_claim, error_msg = policy.can_claim_task(
                task_payload,
                agent_url,
            )
            claim_decision["task_payload"] = task_payload
            claim_decision["reason"] = error_msg
            return can_claim, error_msg

        claimed = task_queue_service.claim_task(
            task_id=task_id,
            agent_url=agent_url,
            lease_until=lease_until,
            idempotency_key=idempotency_key,
            claim_validator=validate_authoritative,
        )
        if claimed is False:
            knowledge_index_denial = claim_decision.get(
                "knowledge_index_denial"
            )
            if isinstance(knowledge_index_denial, dict):
                return knowledge_index_denial
            vector_denial = claim_decision.get(
                "vector_denial"
            )
            if isinstance(vector_denial, dict):
                return vector_denial
            authoritative_payload = claim_decision.get(
                "task_payload"
            )
            lease_info = extract_active_lease(
                authoritative_payload
                if isinstance(authoritative_payload, dict)
                else {}
            )
            reason = str(
                claim_decision.get("reason")
                or "claim_state_changed_or_recovery_gate_closed"
            )
            persist_policy_decision(
                decision_type="execution_claim",
                status="blocked",
                policy_name="task_claim_policy",
                policy_version="claim-v2",
                reasons=[reason],
                details={"agent_url": agent_url},
                task_id=task_id,
                worker_url=agent_url,
            )
            return {
                "error": reason,
                "code": 409,
                "data": {
                    "lease": (
                        lease_info.__dict__
                        if lease_info is not None
                        else {}
                    )
                },
            }
        persist_policy_decision(
            decision_type="execution_claim",
            status="approved",
            policy_name="task_claim_policy",
            policy_version="claim-v2",
            reasons=["lease_granted"],
            details={
                "agent_url": agent_url,
                "lease_seconds": lease_seconds,
                "idempotency_key": idempotency_key,
            },
            task_id=task_id,
            worker_url=agent_url,
        )
        return {"data": {"task_id": task_id, "claimed": True, "lease_until": lease_until}}

    def orchestration_read_model(self, *, task_queue_service) -> dict:
        repos = get_repository_registry()
        tasks = [task.model_dump() for task in repos.task_repo.get_all()]
        model = build_orchestration_read_model(tasks)
        model["recent_policy_decisions"] = [
            {
                "id": item.id,
                "decision_type": item.decision_type,
                "status": item.status,
                "policy_name": item.policy_name,
                "policy_version": item.policy_version,
                "reasons": list(item.reasons or []),
                "task_id": item.task_id,
                "worker_url": item.worker_url,
                "created_at": item.created_at,
            }
            for item in repos.policy_decision_repo.get_all(limit=50)
        ]
        return model


task_claim_service = TaskClaimService()


def get_task_claim_service() -> TaskClaimService:
    return task_claim_service
