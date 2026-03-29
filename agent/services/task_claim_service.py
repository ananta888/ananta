from __future__ import annotations

from agent.routes.tasks.orchestration_policy import compute_lease_expiry, extract_active_lease, persist_policy_decision
from agent.routes.tasks.orchestration_policy.read_model import build_orchestration_read_model
from agent.services.repository_registry import get_repository_registry


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

        task_payload = task.model_dump()
        can_claim, error_msg = policy.can_claim_task(task_payload, agent_url)
        if not can_claim:
            lease_info = extract_active_lease(task_payload)
            persist_policy_decision(
                decision_type="execution_claim",
                status="blocked",
                policy_name="task_claim_policy",
                policy_version="claim-v1",
                reasons=[error_msg or "claim_denied"],
                details={"agent_url": agent_url},
                task_id=task_id,
                worker_url=agent_url,
            )
            return {
                "error": error_msg or "claim_denied",
                "code": 409,
                "data": {"lease": lease_info.__dict__ if lease_info else {}},
            }

        lease_seconds = policy.validate_lease_duration(requested_lease)
        lease_until = compute_lease_expiry(lease_seconds)
        persist_policy_decision(
            decision_type="execution_claim",
            status="approved",
            policy_name="task_claim_policy",
            policy_version="claim-v1",
            reasons=["lease_granted"],
            details={"agent_url": agent_url, "lease_seconds": lease_seconds},
            task_id=task_id,
            worker_url=agent_url,
        )
        task_queue_service.claim_task(
            task_id=task_id,
            agent_url=agent_url,
            lease_until=lease_until,
            idempotency_key=idempotency_key,
        )
        return {"data": {"task_id": task_id, "claimed": True, "lease_until": lease_until}}

    def orchestration_read_model(self, *, task_queue_service) -> dict:
        repos = get_repository_registry()
        tasks = [task.model_dump() for task in repos.task_repo.get_all()]
        model = build_orchestration_read_model(tasks)
        model["recent_policy_decisions"] = [item.model_dump() for item in repos.policy_decision_repo.get_all(limit=50)]
        return model


task_claim_service = TaskClaimService()


def get_task_claim_service() -> TaskClaimService:
    return task_claim_service
