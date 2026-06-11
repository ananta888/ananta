from __future__ import annotations

import time
from typing import Any

from agent.db_models import EvolutionRunDB
from agent.metrics import (
    EVOLUTION_ANALYSES_TOTAL,
    EVOLUTION_OPERATION_DURATION_SECONDS,
    EVOLUTION_PROPOSALS_TOTAL,
)
from agent.services.evolution.models import (
    EvolutionContext,
    EvolutionPolicy,
    EvolutionResult,
    EvolutionTrigger,
    PersistedEvolutionAnalysis,
)
from agent.services.evolution_proposal_service import _bounded_payload
from agent.services.repository_registry import get_repository_registry


def metric_label(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text[:80] or "unknown"


def failure_metric_status(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code:
        return metric_label(f"failed_{code}")
    return "failed"


def failure_audit_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {}
    code = getattr(exc, "code", None)
    if code:
        details["error_code"] = str(code)
    if hasattr(exc, "transient"):
        details["transient"] = bool(getattr(exc, "transient"))
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        details["status_code"] = status_code
    return details


class EvolutionRunService:

    def __init__(self, *, repositories=None, audit_fn=None):
        self._repositories = repositories
        self._audit_fn = audit_fn

    def _audit(self, action: str, details: dict[str, Any]) -> None:
        if self._audit_fn is not None:
            self._audit_fn(action, details)

    def run_read_model(self, run) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "provider_name": run.provider_name,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "trigger_source": run.trigger_source,
            "task_id": run.task_id,
            "goal_id": run.goal_id,
            "trace_id": run.trace_id,
            "plan_id": run.plan_id,
            "summary": run.summary,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "result_metadata": dict(run.result_metadata or {}),
            "provider_metadata": dict(run.provider_metadata or {}),
        }

    def persist_analysis(
        self,
        context: EvolutionContext,
        result: EvolutionResult,
        trigger: EvolutionTrigger,
        *,
        policy: EvolutionPolicy,
        proposal_service=None,
    ) -> PersistedEvolutionAnalysis:
        repos = self._repositories or get_repository_registry()
        run = repos.evolution_run_repo.save(
            EvolutionRunDB(
                id=result.run_id,
                provider_name=result.provider_name,
                status=result.status,
                trigger_type=trigger.trigger_type.value,
                trigger_source=trigger.source,
                task_id=context.task_id,
                goal_id=context.goal_id,
                trace_id=context.trace_id,
                plan_id=context.plan_id,
                context_id=context.context_id,
                summary=result.summary,
                context_refs=list(context.source_refs or []),
                result_metadata={
                    "proposal_count": len(result.proposals),
                    "validation_count": len(result.validation_results),
                    "trigger": trigger.model_dump(mode="json"),
                },
                provider_metadata=_bounded_payload(result.provider_metadata or {}, policy=policy),
                raw_payload=_bounded_payload(result.raw_payload, policy=policy),
            )
        )
        proposal_ids: list[str] = []
        for proposal in result.proposals:
            saved = repos.evolution_proposal_repo.save(
                proposal_service._proposal_db(run, context, result, proposal, policy=policy) if proposal_service else None
            )
            proposal_ids.append(saved.id)
        return PersistedEvolutionAnalysis(
            run_id=run.id,
            provider_name=run.provider_name,
            status=run.status,
            proposal_ids=proposal_ids,
            result=result,
        )

    def record_analysis_metrics(
        self,
        provider_name: str,
        trigger: EvolutionTrigger,
        result: EvolutionResult,
    ) -> None:
        EVOLUTION_ANALYSES_TOTAL.labels(
            provider=metric_label(provider_name),
            trigger_type=metric_label(trigger.trigger_type.value),
            status=metric_label(result.status),
        ).inc()
        for proposal in result.proposals:
            EVOLUTION_PROPOSALS_TOTAL.labels(
                provider=metric_label(provider_name),
                proposal_type=metric_label(proposal.proposal_type),
                risk_level=metric_label(proposal.risk_level),
                requires_review=str(bool(proposal.requires_review)).lower(),
            ).inc()

    def observe_operation_duration(
        self,
        provider_name: str,
        operation: str,
        status: str,
        started_at: float,
    ) -> None:
        EVOLUTION_OPERATION_DURATION_SECONDS.labels(
            provider=metric_label(provider_name),
            operation=metric_label(operation),
            status=metric_label(status),
        ).observe(max(0.0, time.monotonic() - started_at))
