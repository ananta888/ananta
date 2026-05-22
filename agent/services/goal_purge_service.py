from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.exc import ProgrammingError
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
from agent.services.task_admin_service import get_task_admin_service


@dataclass
class GoalPurgeResult:
    goal_id: str
    deleted: dict[str, int]
    prompt_traces_deleted: int
    task_cancel_summary: dict[str, int]
    # PRI-008: worker forward failures are surfaced explicitly.
    worker_cancel_failures: list[dict] = field(default_factory=list)

    @property
    def deleted_total(self) -> int:
        return int(sum(int(v or 0) for v in self.deleted.values()) + int(self.prompt_traces_deleted or 0))

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "deleted": dict(self.deleted),
            "prompt_traces_deleted": int(self.prompt_traces_deleted or 0),
            "task_cancel_summary": dict(self.task_cancel_summary or {}),
            "worker_cancel_failures": list(self.worker_cancel_failures or []),
            "deleted_total": int(self.deleted_total),
        }


class GoalPurgeService:
    """Delete all persisted entities associated with a goal ID."""

    @staticmethod
    def _collect_ids(rows: list) -> list[str]:
        ids: list[str] = []
        for row in rows:
            value = row[0] if isinstance(row, (tuple, list)) else row
            sid = str(value or "").strip()
            if sid:
                ids.append(sid)
        return ids

    @staticmethod
    def _safe_delete(session: Session, stmt) -> int:
        try:
            return int(session.exec(stmt).rowcount or 0)
        except ProgrammingError as exc:
            # Optional tables may not exist on older DB states.
            if "UndefinedTable" in str(exc) or "does not exist" in str(exc):
                session.rollback()
                return 0
            raise

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
            task_ids = self._collect_ids(session.exec(select(TaskDB.id).where(TaskDB.goal_id == goal_id_norm)).all())
            cancel_attempted = 0
            cancel_ok = 0
            cancel_failed = 0
            worker_cancel_failures: list[dict] = []
            for task_id in task_ids:
                cancel_attempted += 1
                ok, _msg, data = get_task_admin_service().intervene_task(
                    task_id=str(task_id),
                    action="cancel",
                    actor="goal_purge",
                )
                if ok:
                    cancel_ok += 1
                    # PRI-008: check worker forward result even on ok transitions.
                    fwd = data.get("worker_cancel_forward") if isinstance(data, dict) else None
                    if isinstance(fwd, dict) and fwd.get("attempted") and fwd.get("status") != "ok":
                        worker_cancel_failures.append({"task_id": str(task_id), **fwd})
                        logging.warning(
                            "goal_purge_worker_cancel_failed goal_id=%s task_id=%s worker_url=%s error=%s",
                            goal_id_norm,
                            task_id,
                            fwd.get("worker_url"),
                            fwd.get("error"),
                        )
                else:
                    cancel_failed += 1
            plan_ids = self._collect_ids(session.exec(select(PlanDB.id).where(PlanDB.goal_id == goal_id_norm)).all())
            planning_run_ids = self._collect_ids(
                session.exec(select(PlanningRunDB.id).where(PlanningRunDB.goal_id == goal_id_norm)).all()
            )
            worker_job_ids = self._collect_ids(
                session.exec(
                    select(WorkerJobDB.id).where(
                        WorkerJobDB.parent_task_id.in_(task_ids) if task_ids else False
                    )
                ).all()
            )

            if worker_job_ids:
                deleted_counts["worker_results_by_worker_job"] = self._safe_delete(
                    session,
                    delete(WorkerResultDB).where(WorkerResultDB.worker_job_id.in_(worker_job_ids)),
                )
            deleted_counts["worker_results_by_task"] = self._safe_delete(
                session,
                delete(WorkerResultDB).where(WorkerResultDB.task_id.in_(task_ids) if task_ids else False),
            )
            if worker_job_ids:
                deleted_counts["worker_jobs"] = self._safe_delete(
                    session,
                    delete(WorkerJobDB).where(WorkerJobDB.id.in_(worker_job_ids)),
                )
            deleted_counts["verification_records"] = self._safe_delete(
                session,
                delete(VerificationRecordDB).where(VerificationRecordDB.goal_id == goal_id_norm),
            )
            deleted_counts["policy_decisions"] = self._safe_delete(
                session,
                delete(PolicyDecisionDB).where(PolicyDecisionDB.goal_id == goal_id_norm),
            )
            deleted_counts["repair_execution_records"] = self._safe_delete(
                session,
                delete(RepairExecutionRecordDB).where(RepairExecutionRecordDB.goal_id == goal_id_norm),
            )
            deleted_counts["planning_review_items"] = self._safe_delete(
                session,
                delete(PlanningReviewItemDB).where(
                    PlanningReviewItemDB.planning_run_id.in_(planning_run_ids) if planning_run_ids else False
                ),
            )
            deleted_counts["planning_evaluations"] = self._safe_delete(
                session,
                delete(PlanningEvaluationDB).where(PlanningEvaluationDB.goal_id == goal_id_norm),
            )
            deleted_counts["planning_runs"] = self._safe_delete(
                session,
                delete(PlanningRunDB).where(PlanningRunDB.goal_id == goal_id_norm),
            )
            deleted_counts["plan_nodes"] = self._safe_delete(
                session,
                delete(PlanNodeDB).where(PlanNodeDB.plan_id.in_(plan_ids) if plan_ids else False),
            )
            deleted_counts["plans"] = self._safe_delete(
                session,
                delete(PlanDB).where(PlanDB.goal_id == goal_id_norm),
            )
            deleted_counts["memory_entries"] = self._safe_delete(
                session,
                delete(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id_norm),
            )
            deleted_counts["retrieval_runs"] = self._safe_delete(
                session,
                delete(RetrievalRunDB).where(RetrievalRunDB.goal_id == goal_id_norm),
            )
            deleted_counts["evolution_proposals"] = self._safe_delete(
                session,
                delete(EvolutionProposalDB).where(EvolutionProposalDB.goal_id == goal_id_norm),
            )
            deleted_counts["evolution_runs"] = self._safe_delete(
                session,
                delete(EvolutionRunDB).where(EvolutionRunDB.goal_id == goal_id_norm),
            )
            deleted_counts["audit_logs_by_goal"] = self._safe_delete(
                session,
                delete(AuditLogDB).where(AuditLogDB.goal_id == goal_id_norm),
            )
            if trace_id:
                deleted_counts["audit_logs_by_trace"] = self._safe_delete(
                    session,
                    delete(AuditLogDB).where(AuditLogDB.trace_id == trace_id),
                )
            deleted_counts["tasks"] = self._safe_delete(
                session,
                delete(TaskDB).where(TaskDB.goal_id == goal_id_norm),
            )
            deleted_counts["goal"] = self._safe_delete(
                session,
                delete(GoalDB).where(GoalDB.id == goal_id_norm),
            )
            session.commit()

        prompt_traces_deleted = 0
        if include_prompt_traces:
            prompt_traces_deleted = int(get_prompt_trace_service().delete_by_goal_id(goal_id_norm))
        return GoalPurgeResult(
            goal_id=goal_id_norm,
            deleted=deleted_counts,
            prompt_traces_deleted=prompt_traces_deleted,
            task_cancel_summary={
                "attempted": int(cancel_attempted),
                "succeeded": int(cancel_ok),
                "failed": int(cancel_failed),
            },
            worker_cancel_failures=worker_cancel_failures,
        )


_SERVICE: GoalPurgeService | None = None


def get_goal_purge_service() -> GoalPurgeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = GoalPurgeService()
    return _SERVICE
