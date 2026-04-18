from __future__ import annotations
import time
from typing import Any, List, Optional
from dataclasses import dataclass, asdict

from agent.services.repository_registry import get_repository_registry
from agent.services.hub_event_service import (
    build_hub_event_catalog,
    build_policy_governance_event,
    build_verification_governance_event,
)
from agent.services.cost_aggregation_service import get_cost_aggregation_service
from agent.db_models import GoalDB, TaskDB, PolicyDecisionDB, VerificationRecordDB


@dataclass
class GovernanceReadModel:
    goal_id: str
    trace_id: str
    status: str
    task_count: int
    routing: dict
    policy: dict
    verification: dict
    cost: dict
    updated_at: float


class GovernanceReadModelService:
    def get_summary(self, goal_id: str, *, include_details: bool = False) -> dict[str, Any] | None:
        repos = get_repository_registry()
        goal = repos.goal_repo.get_by_id(goal_id)
        if not goal:
            return None

        tasks = repos.task_repo.get_by_goal_id(goal_id)
        task_ids = [task.id for task in tasks]

        # Policy Decisions
        policy_decisions = repos.policy_decision_repo.get_by_goal_or_task_ids(goal_id=goal_id, task_ids=task_ids, limit=500)

        # Verification Records
        verification_records = repos.verification_record_repo.get_by_goal_or_task_ids(goal_id=goal_id, task_ids=task_ids, limit=500)

        # Routing Information (from Tasks)
        routing_info = self._get_routing_summary(tasks)

        task_details = []
        if include_details:
            for t in tasks:
                task_details.append({
                    "id": t.id,
                    "status": t.status,
                    "assigned_agent_url": t.assigned_agent_url,
                    "status_reason_code": t.status_reason_code,
                    "verification_status": t.verification_status,
                })

        model = GovernanceReadModel(
            goal_id=goal_id,
            trace_id=goal.trace_id,
            status=goal.status,
            task_count=len(tasks),
            routing={
                **routing_info,
                "tasks": task_details if include_details else []
            } if include_details else {"handoff_count": routing_info.get("handoff_count", 0)},
            policy={
                "total": len(policy_decisions),
                "approved": len([d for d in policy_decisions if d.status == "approved"]),
                "blocked": len([d for d in policy_decisions if d.status == "blocked"]),
                "latest": [d.model_dump() for d in policy_decisions[:5]] if include_details else []
            },
            verification={
                "total": len(verification_records),
                "passed": len([v for v in verification_records if v.status == "passed"]),
                "failed": len([v for v in verification_records if v.status == "failed"]),
                "escalated": len([v for v in verification_records if v.status == "escalated"]),
                "latest": [v.model_dump() for v in verification_records[:5]] if include_details else []
            },
            cost=get_cost_aggregation_service().aggregate_goal_costs(goal_id),
            updated_at=time.time()
        )

        return asdict(model)

    def _get_routing_summary(self, tasks: List[TaskDB]) -> dict[str, Any]:
        handoffs = 0
        agents = set()
        for task in tasks:
            if task.parent_task_id:
                handoffs += 1
            if task.assigned_agent_url:
                agents.add(task.assigned_agent_url)

        return {
            "handoff_count": handoffs,
            "distinct_agents": list(agents),
            "agent_count": len(agents)
        }


_service: Optional[GovernanceReadModelService] = None

def get_governance_read_model_service() -> GovernanceReadModelService:
    global _service
    if _service is None:
        _service = GovernanceReadModelService()
    return _service
