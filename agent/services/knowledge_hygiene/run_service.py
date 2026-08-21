"""Hub-owned, lease-aware Knowledge Hygiene run state machine."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping, Sequence

from agent.repositories.knowledge_hygiene_repository import KnowledgeHygieneRepository
from ananta_contracts.knowledge_hygiene import (
    CoverageState,
    KnowledgeHygieneRun,
    RunState,
    SourceRevisionBinding,
)


class KnowledgeHygieneRunError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class KnowledgeHygieneRunService:
    def __init__(self, repository: KnowledgeHygieneRepository, *, clock: Callable[[], float]) -> None:
        self._repository = repository
        self._clock = clock

    def create(
        self,
        *,
        run_id: str,
        project_id: str,
        source_bindings: Sequence[SourceRevisionBinding],
        policy_version: str,
        profile_name: str,
        budgets: Mapping[str, int],
        actor_id: str,
    ) -> KnowledgeHygieneRun:
        assignment_digest = KnowledgeHygieneRun.calculate_assignment_digest(
            run_id=run_id,
            project_id=project_id,
            source_bindings=source_bindings,
            policy_version=policy_version,
            profile_name=profile_name,
            budgets=budgets,
        )
        now = self._clock()
        run = KnowledgeHygieneRun(
            run_id=run_id,
            project_id=project_id,
            state=RunState.PENDING,
            source_bindings=tuple(source_bindings),
            policy_version=policy_version,
            profile_name=profile_name,
            budgets=dict(budgets),
            actor_id=actor_id,
            coverage=CoverageState.UNKNOWN,
            assignment_digest=assignment_digest,
            created_at=now,
            updated_at=now,
        )
        return self._repository.put_run(run)

    def dispatch(self, *, project_id: str, run_id: str, worker_id: str, lease_seconds: int) -> KnowledgeHygieneRun:
        run = self._require(project_id, run_id)
        if not worker_id.strip():
            raise KnowledgeHygieneRunError("worker_id_required")
        if lease_seconds <= 0:
            raise KnowledgeHygieneRunError("invalid_lease_duration")
        now = self._clock()
        updated = replace(
            run,
            state=RunState.DISPATCHED,
            lease_owner=worker_id,
            lease_expires_at=now + lease_seconds,
            updated_at=now,
        )
        return self._repository.put_run(updated, expected_state=RunState.PENDING.value)

    def checkpoint(self, *, project_id: str, run_id: str, worker_id: str, checkpoint: int) -> KnowledgeHygieneRun:
        run = self._require(project_id, run_id)
        now = self._clock()
        if run.lease_owner != worker_id or run.lease_expires_at is None or run.lease_expires_at < now:
            raise KnowledgeHygieneRunError("run_lease_invalid")
        if checkpoint < run.checkpoint:
            raise KnowledgeHygieneRunError("checkpoint_regression")
        if run.state not in {RunState.DISPATCHED, RunState.RUNNING}:
            raise KnowledgeHygieneRunError("run_not_checkpointable")
        updated = replace(run, state=RunState.RUNNING, checkpoint=checkpoint, updated_at=now)
        return self._repository.put_run(updated, expected_state=run.state.value)

    def finish(
        self,
        *,
        project_id: str,
        run_id: str,
        assignment_digest: str,
        result_digest: str,
        coverage: CoverageState,
    ) -> KnowledgeHygieneRun:
        run = self._require(project_id, run_id)
        if run.assignment_digest != assignment_digest:
            raise KnowledgeHygieneRunError("assignment_digest_mismatch")
        if run.result_digest is not None:
            if run.result_digest != result_digest:
                raise KnowledgeHygieneRunError("result_replay_mismatch")
            return run
        if run.state not in {RunState.DISPATCHED, RunState.RUNNING}:
            raise KnowledgeHygieneRunError("run_not_finishable")
        normalized_coverage = CoverageState(coverage)
        final_state = RunState.COMPLETED if normalized_coverage is CoverageState.COMPLETE else RunState.PARTIAL
        updated = replace(
            run,
            state=final_state,
            coverage=normalized_coverage,
            result_digest=result_digest,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=self._clock(),
        )
        return self._repository.put_run(updated, expected_state=run.state.value)

    def cancel(self, *, project_id: str, run_id: str, actor_id: str) -> KnowledgeHygieneRun:
        run = self._require(project_id, run_id)
        if run.state in {RunState.COMPLETED, RunState.CANCELLED}:
            return run
        updated = replace(
            run,
            state=RunState.CANCELLED,
            actor_id=actor_id,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=self._clock(),
        )
        return self._repository.put_run(updated, expected_state=run.state.value)

    def restart(self, *, project_id: str, prior_run_id: str, new_run_id: str, actor_id: str) -> KnowledgeHygieneRun:
        prior = self._require(project_id, prior_run_id)
        if prior.state not in {RunState.PARTIAL, RunState.FAILED, RunState.CANCELLED}:
            raise KnowledgeHygieneRunError("run_not_restartable")
        return self.create(
            run_id=new_run_id,
            project_id=project_id,
            source_bindings=prior.source_bindings,
            policy_version=prior.policy_version,
            profile_name=prior.profile_name,
            budgets=prior.budgets,
            actor_id=actor_id,
        )

    def _require(self, project_id: str, run_id: str) -> KnowledgeHygieneRun:
        run = self._repository.get_run(project_id, run_id)
        if run is None:
            raise KnowledgeHygieneRunError("run_not_found")
        return run
