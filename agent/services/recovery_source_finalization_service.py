"""Hub-owned finalization of a Recovery source from its approved child DAG."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.recovery_dispatch_gate_service import (
    RecoveryDispatchGateService,
    recovery_accepted_result_digest,
)
from agent.services.recovery_plan_contract import (
    build_recovery_dependency_binding,
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
)
from agent.services.recovery_source_result_projection import (
    RECOVERY_SOURCE_RESULT_SCHEMA,
    validate_recovery_source_artifact_aggregate,
)
from agent.services.recovery_task_mutation_policy import (
    recovery_task_role,
)
from agent.services.task_status_service import normalize_task_status
from agent.services.verification_policy_service import (
    evaluate_quality_gates,
)

_FAILURE_STATUSES = {
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}
_TERMINAL_GOAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _ids(values: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in list(values or [])
            if str(value or "").strip()
        )
    )


def _artifact_path(artifact: dict[str, Any]) -> str:
    return str(
        artifact.get("workspace_relative_path")
        or artifact.get("relative_path")
        or artifact.get("path")
        or artifact.get("name")
        or ""
    ).strip()


@dataclass(frozen=True)
class RecoverySourceFinalizationDecision:
    transitioned: bool
    status: str
    reason_code: str
    child_ids: tuple[str, ...] = ()
    old_status: str | None = None


@dataclass(frozen=True)
class _RecoveryBinding:
    plan_id: str
    goal_id: str
    child_ids: tuple[str, ...]
    source_dependency_ids: tuple[str, ...]
    external_dependency_ids: tuple[str, ...]


class RecoverySourceFinalizationService:
    """Aggregate only Hub-accepted, Hub-verified child results."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        mutation_lock_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._mutation_lock_provider = mutation_lock_provider

    def _repos(self):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry()

    def _locks(self):
        if self._mutation_lock_provider is not None:
            return self._mutation_lock_provider()
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port()

    def finalize_if_ready(
        self,
        *,
        source_task_id: str,
        child_task_ids: list[str] | None = None,
    ) -> RecoverySourceFinalizationDecision:
        """Finalize from PlanNodes, never from a caller-selected child subset."""

        del child_task_ids  # Compatibility input; never authoritative.
        source_id = str(source_task_id or "").strip()
        repos = self._repos()
        initial_source = repos.task_repo.get_by_id(source_id)
        if (
            not source_id
            or initial_source is None
            or recovery_task_role(initial_source) != "source"
        ):
            return RecoverySourceFinalizationDecision(
                False,
                "not_ready",
                "recovery_source_not_finalizable",
            )
        initial_binding, binding_error = self._resolve_binding(
            repos,
            initial_source,
        )
        recovery = _mapping(
            _mapping(
                getattr(
                    initial_source,
                    "status_reason_details",
                    None,
                )
            ).get("model_recovery")
        )
        fallback_child_ids = _ids(
            recovery.get("created_task_ids")
        )
        if initial_binding is None:
            return self._commit_binding_failure(
                repos=repos,
                source=initial_source,
                child_ids=fallback_child_ids,
                reason_code=binding_error,
            )

        from agent.services.lifecycle_service import (
            goal_mutation_lock_id,
        )

        lock_ids = {
            source_id,
            goal_mutation_lock_id(initial_binding.goal_id),
            *initial_binding.child_ids,
            *initial_binding.external_dependency_ids,
        }
        with self._locks().mutation_locks(lock_ids) as acquired:
            if not acquired:
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_lock_unavailable",
                    initial_binding.child_ids,
                )
            source = repos.task_repo.get_by_id(source_id)
            if (
                source is None
                or recovery_task_role(source) != "source"
                or normalize_task_status(
                    getattr(source, "status", None)
                )
                not in {"blocked", "blocked_by_dependency"}
            ):
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_not_finalizable",
                    initial_binding.child_ids,
                )
            binding, binding_error = self._resolve_binding(
                repos,
                source,
            )
            if binding is None or binding != initial_binding:
                return self._commit(
                    repos=repos,
                    source=source,
                    children=[],
                    child_statuses={},
                    target_status="verification_failed",
                    reason_code=(
                        binding_error
                        if binding is None
                        else "recovery_source_binding_changed"
                    ),
                    quality_gate_reason="binding_verification_failed",
                    artifacts=[],
                    output="",
                )

            goal = repos.goal_repo.get_by_id(binding.goal_id)
            if (
                goal is None
                or str(getattr(goal, "status", "") or "")
                .strip()
                .lower()
                in _TERMINAL_GOAL_STATUSES
            ):
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_goal_terminal",
                    binding.child_ids,
                )

            children = [
                repos.task_repo.get_by_id(child_id)
                for child_id in binding.child_ids
            ]
            if any(child is None for child in children):
                return self._commit(
                    repos=repos,
                    source=source,
                    children=[
                        child for child in children if child is not None
                    ],
                    child_statuses={},
                    target_status="verification_failed",
                    reason_code="recovery_source_child_missing",
                    quality_gate_reason="binding_verification_failed",
                    artifacts=[],
                    output="",
                )
            child_rows = [
                child for child in children if child is not None
            ]
            static_binding_error = self._validate_child_bindings(
                repos=repos,
                source=source,
                children=child_rows,
            )
            if static_binding_error:
                return self._commit(
                    repos=repos,
                    source=source,
                    children=child_rows,
                    child_statuses=self._statuses(child_rows),
                    target_status="verification_failed",
                    reason_code=static_binding_error,
                    quality_gate_reason="binding_verification_failed",
                    artifacts=[],
                    output="",
                )

            external_decision = self._evaluate_external_dependencies(
                repos=repos,
                dependency_ids=binding.external_dependency_ids,
            )
            if external_decision == "pending":
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_external_dependency_incomplete",
                    binding.child_ids,
                )
            if external_decision != "completed":
                return self._commit(
                    repos=repos,
                    source=source,
                    children=child_rows,
                    child_statuses=self._statuses(child_rows),
                    target_status="verification_failed",
                    reason_code=external_decision,
                    quality_gate_reason="dependency_verification_failed",
                    artifacts=[],
                    output="",
                )

            child_statuses = self._statuses(child_rows)
            if any(
                status in _FAILURE_STATUSES
                for status in child_statuses.values()
            ):
                return self._commit(
                    repos=repos,
                    source=source,
                    children=child_rows,
                    child_statuses=child_statuses,
                    target_status="verification_failed",
                    reason_code="recovery_child_failed",
                    quality_gate_reason="child_terminal_failure",
                    artifacts=[],
                    output="",
                    result_evidence=self._result_evidence(child_rows),
                )
            if any(
                status != "completed"
                for status in child_statuses.values()
            ):
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_children_incomplete",
                    binding.child_ids,
                )

            result_error = self._validate_accepted_results(child_rows)
            if result_error:
                # All children are already terminal.  A missing/mismatched
                # acceptance proof cannot become valid through waiting, so
                # fail the Hub-owned source deterministically instead of
                # leaving it blocked forever.
                return self._commit(
                    repos=repos,
                    source=source,
                    children=child_rows,
                    child_statuses=child_statuses,
                    target_status="verification_failed",
                    reason_code=result_error,
                    quality_gate_reason=(
                        "result_acceptance_verification_failed"
                    ),
                    artifacts=[],
                    output="",
                    result_evidence=self._result_evidence(child_rows),
                )

            artifacts: list[dict[str, Any]] = []
            outputs: list[str] = []
            failed_child_verifications: list[str] = []
            for child in child_rows:
                child_id = str(getattr(child, "id", "") or "")
                output = str(
                    getattr(child, "last_output", "") or ""
                ).strip()
                if output:
                    outputs.append(f"[{child_id}]\n{output}")
                verification = _mapping(
                    getattr(child, "verification_status", None)
                )
                results = _mapping(verification.get("results"))
                if (
                    str(verification.get("status") or "")
                    .strip()
                    .lower()
                    != "passed"
                    or results.get("final_passed") is not True
                    or not str(
                        verification.get("record_id") or ""
                    ).strip()
                ):
                    failed_child_verifications.append(child_id)
                for artifact in list(
                    verification.get("execution_artifacts") or []
                ):
                    if (
                        isinstance(artifact, dict)
                        and artifact.get("_exists") is True
                        and artifact.get("_hash_verified") is True
                    ):
                        artifacts.append(dict(artifact))

            aggregate_output = "\n\n".join(outputs)[:32_000]
            if (
                validate_recovery_source_artifact_aggregate(
                    artifacts
                )
                is None
            ):
                return self._commit(
                    repos=repos,
                    source=source,
                    children=child_rows,
                    child_statuses=child_statuses,
                    target_status="verification_failed",
                    reason_code=(
                        "recovery_source_artifact_aggregate_invalid"
                    ),
                    quality_gate_reason=(
                        "artifact_aggregate_verification_failed"
                    ),
                    artifacts=[],
                    output=aggregate_output,
                    result_evidence=self._result_evidence(
                        child_rows
                    ),
                )
            quality_passed, quality_reason = evaluate_quality_gates(
                source,
                aggregate_output,
                0,
                policy=None,
            )
            expected_artifacts = [
                dict(value)
                for value in list(
                    _mapping(
                        getattr(source, "verification_spec", None)
                    ).get("expected_artifacts")
                    or []
                )
                if isinstance(value, dict)
                and bool(value.get("required", True))
            ]
            present_paths = {
                path
                for artifact in artifacts
                if (path := _artifact_path(artifact))
            }
            missing_paths = [
                path
                for item in expected_artifacts
                if (
                    path := str(
                        item.get("relative_path")
                        or item.get("path")
                        or ""
                    ).strip()
                )
                and path not in present_paths
            ]
            passed = bool(
                quality_passed
                and not failed_child_verifications
                and not missing_paths
            )
            return self._commit(
                repos=repos,
                source=source,
                children=child_rows,
                child_statuses=child_statuses,
                target_status=(
                    "completed" if passed else "verification_failed"
                ),
                reason_code=(
                    "recovery_children_verified"
                    if passed
                    else "recovery_children_verification_failed"
                ),
                quality_gate_reason=quality_reason,
                artifacts=artifacts,
                output=aggregate_output,
                missing_artifacts=missing_paths,
                failed_child_verifications=failed_child_verifications,
                result_evidence=self._result_evidence(child_rows),
            )

    def _resolve_binding(
        self,
        repos: Any,
        source: Any,
    ) -> tuple[_RecoveryBinding | None, str]:
        details = _mapping(
            getattr(source, "status_reason_details", None)
        )
        recovery = _mapping(details.get("model_recovery"))
        plan_id = str(recovery.get("plan_id") or "").strip()
        goal_id = str(getattr(source, "goal_id", "") or "").strip()
        if not plan_id or not goal_id:
            return None, "recovery_source_binding_incomplete"
        plan = repos.plan_repo.get_by_id(plan_id)
        goal = repos.goal_repo.get_by_id(goal_id)
        nodes = list(
            repos.plan_node_repo.get_by_plan_id(plan_id) or []
        )
        rationale = _mapping(getattr(plan, "rationale", None))
        if plan is None or goal is None or not nodes:
            return None, "recovery_source_plan_binding_missing"
        if (
            str(getattr(plan, "goal_id", "") or "") != goal_id
            or str(rationale.get("source_task_id") or "")
            != str(getattr(source, "id", "") or "")
            or str(getattr(plan, "status", "") or "").lower()
            != "materialized"
            or str(
                rationale.get("materialization_release_state") or ""
            )
            not in {"committed", "completed"}
            or str(rationale.get("plan_digest") or "")
            != calculate_recovery_plan_digest(plan, nodes)
            or str(
                rationale.get("materialization_inputs_digest")
                or ""
            )
            != calculate_recovery_materialization_inputs_digest(goal)
        ):
            return None, "recovery_source_plan_binding_mismatch"
        ordered_nodes = sorted(
            nodes,
            key=lambda node: (
                int(getattr(node, "position", 0) or 0),
                str(getattr(node, "id", "") or ""),
            ),
        )
        child_ids = tuple(
            str(
                getattr(node, "materialized_task_id", "") or ""
            ).strip()
            for node in ordered_nodes
        )
        approved_child_ids = _ids(
            recovery.get("created_task_ids")
        )
        if (
            not child_ids
            or any(not child_id for child_id in child_ids)
            or len(set(child_ids)) != len(child_ids)
            or child_ids != approved_child_ids
        ):
            return None, "recovery_source_child_set_mismatch"
        source_dependencies = _ids(
            getattr(source, "depends_on", None)
        )
        dependency_binding = _mapping(
            recovery.get("dependency_binding")
        )
        plan_binding_digest = str(
            rationale.get(
                "materialization_dependency_binding_digest"
            )
            or ""
        ).strip()
        if dependency_binding or plan_binding_digest:
            try:
                expected_binding = (
                    build_recovery_dependency_binding(
                        source_task_id=str(
                            getattr(source, "id", "") or ""
                        ),
                        preexisting_dependency_ids=list(
                            dependency_binding.get(
                                "preexisting_dependency_ids"
                            )
                            or []
                        ),
                        child_task_ids=list(
                            dependency_binding.get(
                                "child_task_ids"
                            )
                            or []
                        ),
                    )
                )
            except (TypeError, ValueError):
                return (
                    None,
                    "recovery_source_dependency_binding_mismatch",
                )
            if (
                dependency_binding != expected_binding
                or not plan_binding_digest
                or plan_binding_digest
                != str(expected_binding.get("digest") or "")
                or _ids(
                    expected_binding.get("child_task_ids")
                )
                != child_ids
                or _ids(
                    expected_binding.get(
                        "authoritative_dependency_ids"
                    )
                )
                != source_dependencies
            ):
                return (
                    None,
                    "recovery_source_dependency_binding_mismatch",
                )
            external_dependency_ids = _ids(
                expected_binding.get(
                    "preexisting_dependency_ids"
                )
            )
        else:
            # Compatibility path for already-materialized v1 runs. New
            # materializations always carry the exact binding above.
            if not set(child_ids).issubset(source_dependencies):
                return (
                    None,
                    "recovery_source_dependency_binding_mismatch",
                )
            external_dependency_ids = tuple(
                dependency_id
                for dependency_id in source_dependencies
                if dependency_id not in set(child_ids)
            )
        return (
            _RecoveryBinding(
                plan_id=plan_id,
                goal_id=goal_id,
                child_ids=child_ids,
                source_dependency_ids=source_dependencies,
                external_dependency_ids=external_dependency_ids,
            ),
            "",
        )

    @staticmethod
    def _statuses(children: list[Any]) -> dict[str, str]:
        return {
            str(getattr(child, "id", "") or ""): (
                normalize_task_status(
                    getattr(child, "status", None)
                )
            )
            for child in children
        }

    @staticmethod
    def _validate_child_bindings(
        *,
        repos: Any,
        source: Any,
        children: list[Any],
    ) -> str:
        gate = RecoveryDispatchGateService(
            repository_provider=lambda: repos,
        )
        failure_present = any(
            normalize_task_status(getattr(child, "status", None))
            in _FAILURE_STATUSES
            for child in children
        )
        for child in children:
            decision = gate.evaluate_task(
                child,
                repos=repos,
                allow_terminal_task=True,
            )
            if not decision.allowed and not (
                failure_present
                and decision.reason_code
                == "recovery_dispatch_dependency_incomplete"
            ):
                return decision.reason_code
        return ""

    @staticmethod
    def _evaluate_external_dependencies(
        *,
        repos: Any,
        dependency_ids: tuple[str, ...],
    ) -> str:
        statuses = []
        for dependency_id in dependency_ids:
            dependency = repos.task_repo.get_by_id(dependency_id)
            if dependency is None:
                return "recovery_source_external_dependency_missing"
            statuses.append(
                normalize_task_status(
                    getattr(dependency, "status", None)
                )
            )
        if any(status in _FAILURE_STATUSES for status in statuses):
            return "recovery_source_external_dependency_failed"
        if any(status != "completed" for status in statuses):
            return "pending"
        return "completed"

    @staticmethod
    def _validate_accepted_results(children: list[Any]) -> str:
        for child in children:
            details = _mapping(
                getattr(child, "status_reason_details", None)
            )
            lease = _mapping(
                details.get("recovery_dispatch_lease")
            )
            if str(lease.get("state") or "") != "result_accepted":
                return "recovery_child_result_not_accepted"
            if (
                str(lease.get("accepted_result_phase") or "")
                != "execute"
                or str(lease.get("accepted_result_status") or "")
                != "completed"
                or lease.get("accepted_result_terminal") is not True
            ):
                return "recovery_child_terminal_result_not_accepted"
            expected_digest = str(
                lease.get("accepted_result_digest") or ""
            )
            if (
                not expected_digest
                or expected_digest
                != recovery_accepted_result_digest(child)
            ):
                return "recovery_child_result_digest_mismatch"
        return ""

    @staticmethod
    def _result_evidence(children: list[Any]) -> dict[str, Any]:
        return {
            str(getattr(child, "id", "") or ""): {
                "lease": {
                    key: value
                    for key, value in _mapping(
                        _mapping(
                            getattr(
                                child,
                                "status_reason_details",
                                None,
                            )
                        ).get("recovery_dispatch_lease")
                    ).items()
                    if key
                    in {
                        "phase",
                        "state",
                        "accepted_at",
                        "accepted_result_phase",
                        "accepted_result_status",
                        "accepted_result_terminal",
                        "accepted_result_digest",
                    }
                },
                "verification_record_id": _mapping(
                    getattr(child, "verification_status", None)
                ).get("record_id"),
            }
            for child in children
        }

    def _commit_binding_failure(
        self,
        *,
        repos: Any,
        source: Any,
        child_ids: tuple[str, ...],
        reason_code: str,
    ) -> RecoverySourceFinalizationDecision:
        if normalize_task_status(
            getattr(source, "status", None)
        ) not in {"blocked", "blocked_by_dependency"}:
            return RecoverySourceFinalizationDecision(
                False,
                "not_ready",
                reason_code,
                child_ids,
            )
        with self._locks().mutation_locks(
            {
                str(getattr(source, "id", "") or ""),
                *child_ids,
            }
        ) as acquired:
            if not acquired:
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_lock_unavailable",
                    child_ids,
                )
            authoritative = repos.task_repo.get_by_id(
                str(getattr(source, "id", "") or "")
            )
            if authoritative is None:
                return RecoverySourceFinalizationDecision(
                    False,
                    "not_ready",
                    "recovery_source_not_finalizable",
                    child_ids,
                )
            return self._commit(
                repos=repos,
                source=authoritative,
                children=[],
                child_statuses={},
                target_status="verification_failed",
                reason_code=reason_code,
                quality_gate_reason="binding_verification_failed",
                artifacts=[],
                output="",
            )

    def _commit(
        self,
        *,
        repos: Any,
        source: Any,
        children: list[Any],
        child_statuses: dict[str, str],
        target_status: str,
        reason_code: str,
        quality_gate_reason: str,
        artifacts: list[dict[str, Any]],
        output: str,
        missing_artifacts: list[str] | None = None,
        failed_child_verifications: list[str] | None = None,
        result_evidence: dict[str, Any] | None = None,
    ) -> RecoverySourceFinalizationDecision:
        from agent.services.task_runtime_service import (
            append_task_history_event,
        )

        old_status = normalize_task_status(
            getattr(source, "status", None)
        )
        now = time.time()
        details = _mapping(
            getattr(source, "status_reason_details", None)
        )
        verification = _mapping(
            getattr(source, "verification_status", None)
        )
        recovery_state = {
            **_mapping(details.get("model_recovery")),
            "status": (
                "completed"
                if target_status == "completed"
                else "failed"
            ),
            "reason_code": reason_code,
            "completed_at": now,
        }
        strategy_state = {
            **_mapping(details.get("model_recovery_strategy")),
            "status": recovery_state["status"],
            "reason_code": reason_code,
            "updated_at": now,
        }
        child_ids = [
            str(getattr(child, "id", "") or "")
            for child in children
        ]
        evidence = {
            "schema": RECOVERY_SOURCE_RESULT_SCHEMA,
            "status": (
                "passed"
                if target_status == "completed"
                else "failed"
            ),
            "reason_code": reason_code,
            "quality_gate_reason": quality_gate_reason,
            "child_statuses": dict(child_statuses),
            "child_ids": child_ids,
            "accepted_results": dict(result_evidence or {}),
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "missing_artifacts": list(missing_artifacts or []),
            "failed_child_verifications": list(
                failed_child_verifications or []
            ),
            "finalized_at": now,
        }
        transition_key = (
            f"{str(getattr(source, 'id', '') or '')}\0"
            f"{str(recovery_state.get('plan_id') or '')}\0"
            f"{str(recovery_state.get('release_epoch') or '')}\0"
            f"{target_status}\0{reason_code}"
        )
        transition_id = hashlib.sha256(
            transition_key.encode("utf-8")
        ).hexdigest()
        source.status = target_status
        source.status_reason_code = reason_code
        source.status_reason_details = {
            **details,
            "model_recovery": recovery_state,
            "model_recovery_strategy": strategy_state,
            "recovery_source_post_commit": {
                "schema": "ananta.recovery_source_post_commit.v1",
                "state": "pending",
                "transition_status": target_status,
                "transition_reason": reason_code,
                "transition_id": transition_id,
                "old_status": old_status,
                "created_at": now,
            },
        }
        source.verification_status = {
            **verification,
            "model_recovery": recovery_state,
            "model_recovery_strategy": strategy_state,
            "model_recovery_result": evidence,
        }
        source.last_output = output or None
        source.last_exit_code = (
            0 if target_status == "completed" else 1
        )
        source.updated_at = now
        append_task_history_event(
            source,
            event_type="recovery_source_finalized",
            actor="hub_recovery_source_finalizer",
            details={
                "status": target_status,
                "reason_code": reason_code,
                "child_ids": child_ids,
            },
        )
        from agent.common.recovery_source_finalization_write_boundary import (
            authorize_recovery_source_finalization_write,
        )

        with authorize_recovery_source_finalization_write(
            str(getattr(source, "id", "") or "")
        ):
            persisted = repos.task_repo.save(source)
        persisted_status = normalize_task_status(
            getattr(persisted, "status", None)
        )
        if persisted_status != target_status:
            return RecoverySourceFinalizationDecision(
                False,
                "not_ready",
                "recovery_source_commit_rejected",
                tuple(child_ids),
            )
        return RecoverySourceFinalizationDecision(
            True,
            target_status,
            reason_code,
            tuple(child_ids),
            old_status,
        )


_service = RecoverySourceFinalizationService()


def get_recovery_source_finalization_service() -> (
    RecoverySourceFinalizationService
):
    return _service
