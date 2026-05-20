from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import (
    AuditLogDB,
    EvolutionProposalDB,
    EvolutionRunDB,
    GoalDB,
    MemoryEntryDB,
    PlanDB,
    PlanNodeDB,
    PlanningEvaluationDB,
    PlanningReviewItemDB,
    PlanningRunDB,
    PolicyDecisionDB,
    RepairExecutionRecordDB,
    RetrievalRunDB,
    TaskDB,
    VerificationRecordDB,
    WorkerJobDB,
    WorkerResultDB,
)
from agent.services.prompt_trace_service import get_prompt_trace_service


@dataclass(frozen=True)
class GoalPurgeResult:
    goal_id: str
    deleted: dict[str, int]
    prompt_traces_deleted: int

    @property
    def deleted_total(self) -> int:
        return int(sum(int(v or 0) for v in self.deleted.values()) + int(self.prompt_traces_deleted or 0))

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "deleted": dict(self.deleted),
            "prompt_traces_deleted": int(self.prompt_traces_deleted or 0),
            "deleted_total": int(self.deleted_total),
        }


class GoalPurgeService:
    """Delete all persisted entities associated with a goal ID."""

    def purge_goal(self, goal_id: str, *, include_prompt_traces: bool = True) -> GoalPurgeResult | None:
        goal_id_norm = str(goal_id or "").strip()
        if not goal_id_norm:
            return None
        deleted_counts: dict[str, int] = {}
        with Session(engine) as session:
            goal = session.get(GoalDB, goal_id_norm)
            if goal is None:
                return None

            trace_id = str(getattr(goal, "trace_id", "") or "").strip()
            task_ids = [
                row[0]
                for row in session.exec(select(TaskDB.id).where(TaskDB.goal_id == goal_id_norm)).all()
                if row and row[0]
            ]
            plan_ids = [
                row[0]
                for row in session.exec(select(PlanDB.id).where(PlanDB.goal_id == goal_id_norm)).all()
                if row and row[0]
            ]
            planning_run_ids = [
                row[0]
                for row in session.exec(select(PlanningRunDB.id).where(PlanningRunDB.goal_id == goal_id_norm)).all()
                if row and row[0]
            ]
            worker_job_ids = [
                row[0]
                for row in session.exec(
                    select(WorkerJobDB.id).where(
                        WorkerJobDB.parent_task_id.in_(task_ids) if task_ids else False
                    )
                ).all()
                if row and row[0]
            ]

            if worker_job_ids:
                deleted_counts["worker_results_by_worker_job"] = session.exec(
                    delete(WorkerResultDB).where(WorkerResultDB.worker_job_id.in_(worker_job_ids))
                ).rowcount or 0
            deleted_counts["worker_results_by_task"] = session.exec(
                delete(WorkerResultDB).where(WorkerResultDB.task_id.in_(task_ids) if task_ids else False)
            ).rowcount or 0
            if worker_job_ids:
                deleted_counts["worker_jobs"] = session.exec(
                    delete(WorkerJobDB).where(WorkerJobDB.id.in_(worker_job_ids))
                ).rowcount or 0
            deleted_counts["verification_records"] = session.exec(
                delete(VerificationRecordDB).where(VerificationRecordDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["policy_decisions"] = session.exec(
                delete(PolicyDecisionDB).where(PolicyDecisionDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["repair_execution_records"] = session.exec(
                delete(RepairExecutionRecordDB).where(RepairExecutionRecordDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["planning_review_items"] = session.exec(
                delete(PlanningReviewItemDB).where(
                    PlanningReviewItemDB.planning_run_id.in_(planning_run_ids) if planning_run_ids else False
                )
            ).rowcount or 0
            deleted_counts["planning_evaluations"] = session.exec(
                delete(PlanningEvaluationDB).where(PlanningEvaluationDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["planning_runs"] = session.exec(
                delete(PlanningRunDB).where(PlanningRunDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["plan_nodes"] = session.exec(
                delete(PlanNodeDB).where(PlanNodeDB.plan_id.in_(plan_ids) if plan_ids else False)
            ).rowcount or 0
            deleted_counts["plans"] = session.exec(
                delete(PlanDB).where(PlanDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["memory_entries"] = session.exec(
                delete(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["retrieval_runs"] = session.exec(
                delete(RetrievalRunDB).where(RetrievalRunDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["evolution_proposals"] = session.exec(
                delete(EvolutionProposalDB).where(EvolutionProposalDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["evolution_runs"] = session.exec(
                delete(EvolutionRunDB).where(EvolutionRunDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["audit_logs_by_goal"] = session.exec(
                delete(AuditLogDB).where(AuditLogDB.goal_id == goal_id_norm)
            ).rowcount or 0
            if trace_id:
                deleted_counts["audit_logs_by_trace"] = session.exec(
                    delete(AuditLogDB).where(AuditLogDB.trace_id == trace_id)
                ).rowcount or 0
            deleted_counts["tasks"] = session.exec(
                delete(TaskDB).where(TaskDB.goal_id == goal_id_norm)
            ).rowcount or 0
            deleted_counts["goal"] = session.exec(
                delete(GoalDB).where(GoalDB.id == goal_id_norm)
            ).rowcount or 0
            session.commit()

        prompt_traces_deleted = 0
        if include_prompt_traces:
            prompt_traces_deleted = int(get_prompt_trace_service().delete_by_goal_id(goal_id_norm))
        return GoalPurgeResult(
            goal_id=goal_id_norm,
            deleted=deleted_counts,
            prompt_traces_deleted=prompt_traces_deleted,
        )


_SERVICE: GoalPurgeService | None = None


def get_goal_purge_service() -> GoalPurgeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = GoalPurgeService()
    return _SERVICE
