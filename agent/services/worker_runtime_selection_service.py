"""Deterministic worker/runtime selection service.

Implements the first practical backend slice for DRR-T048. The service chooses
one worker and one concrete runtime target from validated policies and candidate
metadata. It is deterministic and records rejected candidates with reason codes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from worker.core.runtime_target import (
    RejectedWorkerRuntimeCandidate,
    RuntimeHealthState,
    SelectionDecisionStatus,
    WorkerCandidate,
    WorkerKind,
    WorkerRuntimeKind,
    WorkerRuntimeSelectionDecision,
    WorkerRuntimeTarget,
    WorkerSelectionMode,
    WorkerSelectionPolicy,
)


_MUTATION_CAPABILITIES = {
    "patch_apply",
    "shell_execute",
    "file_write",
    "memory_write",
    "repair.execute.low_risk",
    "repair.execute.approval_gated",
    "repair.rollback",
}

_ANALYSIS_ONLY_WORKER_KINDS = {WorkerKind.hermes, WorkerKind.shellgpt}


@dataclass(frozen=True)
class WorkerRuntimeSelectionRequest:
    policy: WorkerSelectionPolicy
    workers: list[WorkerCandidate]
    runtime_targets: list[WorkerRuntimeTarget]
    required_capabilities: list[str]
    execution_mode: str = ""
    policy_decision_ref: str = ""
    context_boundary_decision: str = "not_evaluated"
    required_context_class: str = ""  # Added for CAP integration
    context_access_policy: Optional[Any] = None # Added for CAP integration


class WorkerRuntimeSelectionService:
    """Select a worker/runtime pair using strict deterministic rules."""

    def select(self, request: WorkerRuntimeSelectionRequest) -> WorkerRuntimeSelectionDecision:
        policy = request.policy
        required = _dedupe([*policy.required_capabilities, *request.required_capabilities])
        rejected: list[RejectedWorkerRuntimeCandidate] = []

        worker_by_id = {w.worker_id: w for w in request.workers}
        runtime_by_id = {r.runtime_target_id: r for r in request.runtime_targets}

        if policy.mode == WorkerSelectionMode.fixed:
            candidates = self._fixed_candidates(policy, request.workers)
        else:
            candidates = list(request.workers)

        scored: list[tuple[int, WorkerCandidate, WorkerRuntimeTarget]] = []
        for worker in sorted(candidates, key=lambda w: (w.priority, w.worker_kind.value, w.worker_id)):
            worker_allowed, worker_rejection = self._check_worker(policy, worker, required, request.execution_mode)
            if not worker_allowed:
                rejected.append(worker_rejection)
                continue

            runtimes = self._candidate_runtimes(worker, runtime_by_id, request.runtime_targets)
            if not runtimes:
                rejected.append(RejectedWorkerRuntimeCandidate(
                    worker_id=worker.worker_id,
                    worker_kind=worker.worker_kind,
                    reason_code="no_runtime_target",
                ))
                continue

            any_runtime_ok = False
            for runtime in sorted(runtimes, key=lambda r: (self._runtime_score(policy, r), r.runtime_target_id)):
                runtime_allowed, runtime_rejection = self._check_runtime(policy, worker, runtime, required)
                if not runtime_allowed:
                    rejected.append(runtime_rejection)
                    continue

                # CAP-BE-T021: Integrate context policy
                if request.context_access_policy and request.required_context_class:
                     # Check if this worker/runtime is allowed to receive the required context class
                     # This is a simplified check for now
                     if not self._is_context_allowed(request.context_access_policy, worker, runtime, request.required_context_class):
                         rejected.append(RejectedWorkerRuntimeCandidate(
                             worker_id=worker.worker_id,
                             worker_kind=worker.worker_kind,
                             runtime_target_id=runtime.runtime_target_id,
                             reason_code="context_class_not_allowed",
                         ))
                         continue

                any_runtime_ok = True
                score = self._score(policy, worker, runtime, required)
                scored.append((score, worker, runtime))
            if not any_runtime_ok and policy.mode == WorkerSelectionMode.fixed:
                # Fixed assignment must fail closed; keep all runtime rejections above.
                pass

        if not scored:
            return WorkerRuntimeSelectionDecision(
                selection_mode=policy.mode,
                decision_status=SelectionDecisionStatus.no_eligible_worker,
                rejected_candidates=rejected,
                required_capabilities=required,
                missing_capabilities=required,
                policy_decision_ref=request.policy_decision_ref,
                context_boundary_decision=request.context_boundary_decision,
            )

        score, selected_worker, selected_runtime = sorted(
            scored,
            key=lambda item: (item[0], item[1].priority, item[1].worker_kind.value, item[1].worker_id, item[2].runtime_target_id),
        )[0]
        return WorkerRuntimeSelectionDecision(
            selected_worker_id=selected_worker.worker_id,
            selected_worker_kind=selected_worker.worker_kind,
            selected_runtime_target_id=selected_runtime.runtime_target_id,
            selected_runtime_kind=selected_runtime.runtime_kind,
            selection_mode=policy.mode,
            decision_status=SelectionDecisionStatus.selected,
            selected_reason=self._selected_reason(policy, selected_worker, selected_runtime, score),
            rejected_candidates=rejected,
            required_capabilities=required,
            policy_decision_ref=request.policy_decision_ref,
            context_boundary_decision=request.context_boundary_decision,
        )

    def _fixed_candidates(self, policy: WorkerSelectionPolicy, workers: list[WorkerCandidate]) -> list[WorkerCandidate]:
        if policy.fixed_worker_id:
            return [w for w in workers if w.worker_id == policy.fixed_worker_id]
        if policy.fixed_worker_kind:
            return [w for w in workers if w.worker_kind == policy.fixed_worker_kind]
        return []

    def _is_context_allowed(self, policy: Any, worker: WorkerCandidate, runtime: WorkerRuntimeTarget, context_class: str) -> bool:
        # CAP-BE-T021: Simplified context allowance check
        # In a real implementation, this would use ContextAccessPolicyService.get_decision
        # for a representative block of the requested class.

        # If it's a secret class, only allow local models and tools
        if context_class in ["secret", "credential", "security_sensitive"]:
            if runtime.runtime_kind == WorkerRuntimeKind.cloud:
                return False
        return True

    def _check_worker(
        self,
        policy: WorkerSelectionPolicy,
        worker: WorkerCandidate,
        required: list[str],
        execution_mode: str,
    ) -> tuple[bool, RejectedWorkerRuntimeCandidate]:
        if worker.health_state not in {RuntimeHealthState.ready, RuntimeHealthState.degraded}:
            return False, self._reject_worker(worker, "worker_unavailable")
        if worker.validation_errors:
            return False, self._reject_worker(worker, "worker_validation_errors")
        if not policy.allows_worker_kind(worker.worker_kind):
            if worker.worker_kind in policy.denied_worker_kinds:
                reason = "worker_kind_denied_by_policy"
            elif worker.worker_kind in {WorkerKind.hermes, WorkerKind.remote_worker, WorkerKind.custom_worker} and not policy.allow_external_workers:
                reason = "external_worker_denied_allow_external_false"
            else:
                reason = "worker_kind_not_allowed"
            return False, self._reject_worker(worker, reason)
        ok, missing = worker.supports_capabilities(required)
        if not ok:
            return False, self._reject_worker(worker, "missing_worker_capabilities", missing)
        if any(cap in _MUTATION_CAPABILITIES for cap in required) and worker.worker_kind in _ANALYSIS_ONLY_WORKER_KINDS:
            return False, self._reject_worker(worker, "analysis_only_worker_rejected_for_mutation")
        if execution_mode and worker.supported_execution_modes and execution_mode not in worker.supported_execution_modes:
            return False, self._reject_worker(worker, "execution_mode_not_supported")
        return True, self._reject_worker(worker, "")

    def _check_runtime(
        self,
        policy: WorkerSelectionPolicy,
        worker: WorkerCandidate,
        runtime: WorkerRuntimeTarget,
        required: list[str],
    ) -> tuple[bool, RejectedWorkerRuntimeCandidate]:
        if runtime.health_state not in {RuntimeHealthState.ready, RuntimeHealthState.degraded}:
            return False, self._reject(worker, runtime, "runtime_unavailable")
        if runtime.validation_errors:
            return False, self._reject(worker, runtime, "runtime_validation_errors")
        if runtime.is_cloud and not policy.allow_cloud:
            return False, self._reject(worker, runtime, "cloud_runtime_denied_allow_cloud_false")
        if runtime.is_external and not policy.allow_external_workers:
            return False, self._reject(worker, runtime, "external_runtime_denied_allow_external_false")
        ok, missing = runtime.supports_capabilities(required)
        if not ok:
            return False, self._reject(worker, runtime, "missing_runtime_capabilities", missing)
        return True, self._reject(worker, runtime, "")

    def _candidate_runtimes(
        self,
        worker: WorkerCandidate,
        runtime_by_id: dict[str, WorkerRuntimeTarget],
        all_runtimes: list[WorkerRuntimeTarget],
    ) -> list[WorkerRuntimeTarget]:
        if worker.runtime_target_ids:
            return [runtime_by_id[rid] for rid in worker.runtime_target_ids if rid in runtime_by_id]
        return all_runtimes

    def _score(
        self,
        policy: WorkerSelectionPolicy,
        worker: WorkerCandidate,
        runtime: WorkerRuntimeTarget,
        required: list[str],
    ) -> int:
        score = worker.priority
        score += self._runtime_score(policy, runtime)
        if worker.worker_kind == WorkerKind.native_ananta_worker and any(cap.startswith("repair.execute") for cap in required):
            score -= 40
        if worker.worker_kind == WorkerKind.opencode and "patch_propose" in required:
            score -= 20
        if worker.worker_kind == WorkerKind.hermes:
            score += 30
        if policy.prefer_local and runtime.is_local:
            score -= 25
        if runtime.health_state == RuntimeHealthState.degraded:
            score += 25
        return score

    def _runtime_score(self, policy: WorkerSelectionPolicy, runtime: WorkerRuntimeTarget) -> int:
        if runtime.is_local:
            return 0 if policy.prefer_local else 10
        if runtime.is_cloud:
            return 100
        if runtime.is_external:
            return 70
        return 40

    def _selected_reason(self, policy: WorkerSelectionPolicy, worker: WorkerCandidate, runtime: WorkerRuntimeTarget, score: int) -> str:
        return (
            f"selected {worker.worker_kind.value}/{worker.worker_id} on "
            f"{runtime.runtime_kind.value}/{runtime.runtime_target_id} "
            f"using {policy.mode.value}; score={score}"
        )

    def _reject_worker(
        self,
        worker: WorkerCandidate,
        reason_code: str,
        missing: list[str] | None = None,
    ) -> RejectedWorkerRuntimeCandidate:
        return RejectedWorkerRuntimeCandidate(
            worker_id=worker.worker_id,
            worker_kind=worker.worker_kind,
            reason_code=reason_code,
            missing_capabilities=missing or [],
        )

    def _reject(
        self,
        worker: WorkerCandidate,
        runtime: WorkerRuntimeTarget,
        reason_code: str,
        missing: list[str] | None = None,
    ) -> RejectedWorkerRuntimeCandidate:
        return RejectedWorkerRuntimeCandidate(
            worker_id=worker.worker_id,
            worker_kind=worker.worker_kind,
            runtime_target_id=runtime.runtime_target_id,
            runtime_kind=runtime.runtime_kind,
            reason_code=reason_code,
            missing_capabilities=missing or [],
        )


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        v = str(value or "").strip()
        if v and v not in result:
            result.append(v)
    return result


__all__ = ["WorkerRuntimeSelectionRequest", "WorkerRuntimeSelectionService"]
