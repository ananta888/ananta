"""Claim-time fence for Hub-materialized recovery tasks."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import logging
import secrets
import time
from typing import Any, Callable, Iterator, Mapping

from agent.services.recovery_dispatch_contract import (
    _RESULT_CANDIDATE_SCHEMA,
    RecoveryDispatchGateDecision,
    RecoveryDispatchLease,
    _mapping,
    _value,
)
from agent.services.recovery_dispatch_contract import (
    build_recovery_result_candidate as build_recovery_result_candidate,
)
from agent.services.recovery_dispatch_contract import (
    recovery_accepted_result_digest as recovery_accepted_result_digest,
)
from agent.services.recovery_dispatch_contract import (
    recovery_dispatch_request_fingerprint as recovery_dispatch_request_fingerprint,
)
from agent.services.recovery_dispatch_contract import (
    task_copy as _task_copy,
)
from agent.services.recovery_plan_contract import (
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)

_TERMINAL_GOAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
}
_TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}
_DISPATCHABLE_RECOVERY_STATUSES = {
    "todo",
    "created",
    "assigned",
    "proposing",
    "in_progress",
    "delegated",
    "updated",
}
_SUCCESSFUL_DEPENDENCY_STATUSES = {"completed"}
_LOG = logging.getLogger(__name__)


class RecoveryDispatchGateService:
    """Validate persisted release ownership immediately before a Hub claim."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        mutation_lock_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._mutation_lock_provider = mutation_lock_provider

    def _repos(self, app: Any | None = None):
        if self._repository_provider is not None:
            return self._repository_provider()
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        return get_repository_registry(app)

    def _lock_port(self):
        if self._mutation_lock_provider is not None:
            return self._mutation_lock_provider()
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )

        return get_task_mutation_lock_port()

    @staticmethod
    def _is_recovery_child(task: Any) -> bool:
        details = _mapping(
            _value(task, "status_reason_details")
        )
        return bool(
            str(
                _value(task, "derivation_reason") or ""
            )
            == "goal_task_recovery"
            or _mapping(details.get("model_recovery_release"))
        )

    @classmethod
    def _is_recovery_source(cls, task: Any) -> bool:
        if task is None or cls._is_recovery_child(task):
            return False
        details = _mapping(_value(task, "status_reason_details"))
        verification = _mapping(_value(task, "verification_status"))
        return bool(
            _mapping(details.get("model_recovery"))
            or _mapping(details.get("model_recovery_strategy"))
            or _mapping(verification.get("model_recovery"))
            or _mapping(
                verification.get("model_recovery_strategy")
            )
        )

    @classmethod
    def is_recovery_child(cls, task: Any) -> bool:
        """Public predicate shared by Hub and Worker admission boundaries."""

        return cls._is_recovery_child(task)

    @classmethod
    def is_recovery_source(cls, task: Any) -> bool:
        return cls._is_recovery_source(task)

    @staticmethod
    def _accepted_terminal_result_is_proven(
        task: Any,
        lease: Mapping[str, Any],
    ) -> bool:
        """Accept a terminal race winner only with its complete Hub proof."""

        status = str(_value(task, "status") or "").strip().lower()
        expected_digest = str(
            lease.get("accepted_result_digest") or ""
        )
        return bool(
            status in _TERMINAL_TASK_STATUSES
            and str(lease.get("state") or "") == "result_accepted"
            and lease.get("accepted_result_terminal") is True
            and str(lease.get("accepted_result_phase") or "")
            == "execute"
            and str(lease.get("accepted_result_status") or "")
            == status
            and len(expected_digest) == 64
            and hmac.compare_digest(
                expected_digest,
                recovery_accepted_result_digest(task),
            )
        )

    @staticmethod
    def _validated_result_candidate(
        task: Any,
        *,
        phase: str,
    ) -> str:
        """Return the Hub-derived terminal status staged for atomic publish."""

        details = _mapping(_value(task, "status_reason_details"))
        candidate = _mapping(
            details.get("recovery_result_candidate")
        )
        lease = _mapping(details.get("recovery_dispatch_lease"))
        task_id = str(_value(task, "id") or "")
        status = str(candidate.get("status") or "").strip().lower()
        verification = _mapping(_value(task, "verification_status"))
        verification_results = _mapping(
            verification.get("results")
        )
        try:
            candidate_revision = int(
                candidate.get("lease_revision") or 0
            )
            lease_revision = int(lease.get("revision") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "recovery_result_candidate_binding_invalid"
            ) from exc
        if (
            str(candidate.get("schema") or "")
            != _RESULT_CANDIDATE_SCHEMA
            or str(candidate.get("task_id") or "") != task_id
            or str(candidate.get("phase") or "") != phase
            or str(candidate.get("state") or "") != "staged"
            or status not in {"completed", "verification_failed"}
            or candidate_revision != lease_revision
            or not hmac.compare_digest(
                str(candidate.get("lease_token_digest") or ""),
                str(lease.get("token_digest") or ""),
            )
            or not hmac.compare_digest(
                str(candidate.get("request_fingerprint") or ""),
                str(lease.get("request_fingerprint") or ""),
            )
            or not str(candidate.get("verification_record_id") or "")
            or str(candidate.get("verification_record_id") or "")
            != str(verification.get("record_id") or "")
        ):
            raise RuntimeError(
                "recovery_result_candidate_binding_invalid"
            )
        verification_passed = bool(
            str(verification.get("status") or "").strip().lower()
            == "passed"
            and verification_results.get("final_passed") is True
        )
        if (status == "completed") != verification_passed:
            raise RuntimeError(
                "recovery_result_candidate_verification_mismatch"
            )
        return status

    def evaluate_task(
        self,
        task: Any,
        *,
        app: Any | None = None,
        repos: Any | None = None,
        allow_terminal_task: bool = False,
    ) -> RecoveryDispatchGateDecision:
        if task is None:
            return RecoveryDispatchGateDecision(
                False,
                "task_not_found",
            )
        if self._is_recovery_source(task):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_source_not_executable",
                source_task_id=str(_value(task, "id") or "")
                or None,
            )
        if not self._is_recovery_child(task):
            return RecoveryDispatchGateDecision(
                True,
                "not_recovery_child",
            )

        repos = repos or self._repos(app)
        plan_id = str(_value(task, "plan_id") or "").strip()
        source_task_id = str(
            _value(task, "source_task_id") or ""
        ).strip()
        goal_id = str(_value(task, "goal_id") or "").strip()
        child_team_id = str(
            _value(task, "team_id") or ""
        ).strip()
        child_status = str(
            _value(task, "status") or ""
        ).strip().lower()
        if (
            child_status in _TERMINAL_TASK_STATUSES
            and not allow_terminal_task
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_task_terminal",
                source_task_id=source_task_id or None,
                plan_id=plan_id or None,
            )
        if (
            child_status not in _DISPATCHABLE_RECOVERY_STATUSES
            and not (
                allow_terminal_task
                and child_status in _TERMINAL_TASK_STATUSES
            )
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_status_not_dispatchable",
                source_task_id=source_task_id or None,
                plan_id=plan_id or None,
            )
        if not all(
            (plan_id, source_task_id, goal_id, child_team_id)
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_binding_incomplete",
                source_task_id=source_task_id or None,
                plan_id=plan_id or None,
            )

        plan = repos.plan_repo.get_by_id(plan_id)
        source = repos.task_repo.get_by_id(source_task_id)
        goal = repos.goal_repo.get_by_id(goal_id)
        if plan is None or source is None or goal is None:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_owner_missing",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )

        rationale = _mapping(getattr(plan, "rationale", None))
        if str(
            rationale.get("materialization_inputs_digest") or ""
        ) != calculate_recovery_materialization_inputs_digest(goal):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_materialization_inputs_changed",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        nodes = list(
            repos.plan_node_repo.get_by_plan_id(plan_id) or []
        )
        current_plan_digest = calculate_recovery_plan_digest(
            plan,
            nodes,
        )
        if (
            not nodes
            or str(rationale.get("plan_digest") or "")
            != current_plan_digest
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_plan_digest_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        child_node = next(
            (
                node
                for node in nodes
                if str(
                    getattr(node, "materialized_task_id", "")
                    or ""
                )
                == str(_value(task, "id") or "")
                and str(getattr(node, "id", "") or "")
                == str(_value(task, "plan_node_id") or "")
            ),
            None,
        )
        if child_node is None:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_plan_node_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        node_rationale = _mapping(
            getattr(child_node, "rationale", None)
        )
        expected_node_payload = {
            "title": str(
                getattr(child_node, "title", "") or ""
            ),
            "description": str(
                getattr(child_node, "description", "") or ""
            ),
            "priority": str(
                getattr(child_node, "priority", "") or ""
            ),
            "task_kind": str(
                node_rationale.get("task_kind") or ""
            ),
            "retrieval_intent": str(
                node_rationale.get("retrieval_intent") or ""
            ),
            "required_context_scope": str(
                node_rationale.get("required_context_scope") or ""
            ),
            "preferred_bundle_mode": str(
                node_rationale.get("preferred_bundle_mode") or ""
            ),
            "required_capabilities": list(
                node_rationale.get("required_capabilities") or []
            ),
            "verification_spec": _mapping(
                getattr(child_node, "verification_spec", None)
            ),
        }
        actual_node_payload = {
            "title": str(_value(task, "title") or ""),
            "description": str(
                _value(task, "description") or ""
            ),
            "priority": str(_value(task, "priority") or ""),
            "task_kind": str(
                _value(task, "task_kind") or ""
            ),
            "retrieval_intent": str(
                _value(task, "retrieval_intent") or ""
            ),
            "required_context_scope": str(
                _value(task, "required_context_scope") or ""
            ),
            "preferred_bundle_mode": str(
                _value(task, "preferred_bundle_mode") or ""
            ),
            "required_capabilities": list(
                _value(task, "required_capabilities") or []
            ),
            "verification_spec": _mapping(
                _value(task, "verification_spec")
            ),
        }
        if actual_node_payload != expected_node_payload:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_plan_node_payload_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        task_ids_by_node_key = {
            str(getattr(node, "node_key", "") or ""): str(
                getattr(node, "materialized_task_id", "") or ""
            )
            for node in nodes
        }
        expected_dependencies = [
            task_ids_by_node_key[str(node_key)]
            for node_key in list(
                getattr(child_node, "depends_on", None) or []
            )
            if task_ids_by_node_key.get(str(node_key))
        ]
        if list(_value(task, "depends_on") or []) != (
            expected_dependencies
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_dependency_binding_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        for dependency_id in expected_dependencies:
            dependency = repos.task_repo.get_by_id(dependency_id)
            dependency_status = str(
                _value(dependency, "status") or ""
            ).strip().lower()
            if (
                dependency is None
                or dependency_status
                not in _SUCCESSFUL_DEPENDENCY_STATUSES
            ):
                return RecoveryDispatchGateDecision(
                    False,
                    "recovery_dispatch_dependency_incomplete",
                    source_task_id=source_task_id,
                    plan_id=plan_id,
                )
        source_recovery = _mapping(
            _mapping(
                _value(source, "status_reason_details")
            ).get("model_recovery")
        )
        child_release = _mapping(
            _mapping(
                _value(task, "status_reason_details")
            ).get("model_recovery_release")
        )
        approved_payload_digest = str(
            child_release.get("task_payload_digest") or ""
        )
        if (
            not approved_payload_digest
            or not hmac.compare_digest(
                approved_payload_digest,
                calculate_recovery_task_payload_digest(task),
            )
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_payload_digest_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )
        release_state = str(
            rationale.get("materialization_release_state") or ""
        ).strip()
        release_epoch = str(
            rationale.get("materialization_release_epoch") or ""
        ).strip()
        source_status = str(
            _value(source, "status") or ""
        ).strip().lower()
        goal_status = str(
            _value(goal, "status") or ""
        ).strip().lower()
        source_team_id = str(
            _value(source, "team_id") or ""
        ).strip()
        goal_team_id = str(
            _value(goal, "team_id") or ""
        ).strip()
        plan_team_id = str(
            rationale.get("team_id") or ""
        ).strip()
        approval_id = str(
            rationale.get(
                "materialization_release_approval_id"
            )
            or ""
        ).strip()
        recovery_key = str(
            rationale.get("recovery_key") or ""
        ).strip()

        if (
            source_status in _TERMINAL_TASK_STATUSES
            or goal_status in _TERMINAL_GOAL_STATUSES
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_owner_terminal",
                source_task_id=source_task_id,
                plan_id=plan_id,
                release_epoch=release_epoch or None,
            )
        if release_state not in {"committed", "completed"}:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_release_not_committed",
                source_task_id=source_task_id,
                plan_id=plan_id,
                release_epoch=release_epoch or None,
            )
        if not (
            str(_value(plan, "goal_id") or "") == goal_id
            and str(
                _value(plan, "status") or ""
            ).strip().lower()
            == "materialized"
            and str(_value(source, "goal_id") or "") == goal_id
            and str(rationale.get("source_task_id") or "")
            == source_task_id
            and str(source_recovery.get("plan_id") or "") == plan_id
            and source_status == "blocked_by_dependency"
            and plan_team_id
            and plan_team_id
            == child_team_id
            == source_team_id
            == goal_team_id
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_binding_mismatch",
                source_task_id=source_task_id,
                plan_id=plan_id,
                release_epoch=release_epoch or None,
            )

        if release_epoch:
            if not (
                str(child_release.get("release_epoch") or "")
                == release_epoch
                and str(child_release.get("plan_id") or "")
                == plan_id
                and str(child_release.get("source_task_id") or "")
                == source_task_id
                and str(child_release.get("goal_id") or "")
                == goal_id
                and str(child_release.get("team_id") or "")
                == plan_team_id
                and str(source_recovery.get("release_epoch") or "")
                == release_epoch
                and str(
                    rationale.get(
                        "materialization_release_source_task_id"
                    )
                    or ""
                )
                == source_task_id
                and str(
                    rationale.get(
                        "materialization_release_goal_id"
                    )
                    or ""
                )
                == goal_id
                and str(
                    rationale.get(
                        "materialization_release_team_id"
                    )
                    or ""
                )
                == plan_team_id
                and approval_id
                and approval_id
                == str(
                    source_recovery.get(
                        "approval_request_id"
                    )
                    or ""
                )
                == str(
                    child_release.get(
                        "approval_request_id"
                    )
                    or ""
                )
                and recovery_key
                and recovery_key
                == str(
                    source_recovery.get("recovery_key") or ""
                )
                == str(
                    child_release.get("recovery_key") or ""
                )
            ):
                return RecoveryDispatchGateDecision(
                    False,
                    "recovery_release_epoch_mismatch",
                    source_task_id=source_task_id,
                    plan_id=plan_id,
                    release_epoch=release_epoch,
                )
        elif child_release:
            # New-format children may never fall back to the legacy path.
            return RecoveryDispatchGateDecision(
                False,
                "recovery_release_epoch_missing",
                source_task_id=source_task_id,
                plan_id=plan_id,
            )

        return RecoveryDispatchGateDecision(
            True,
            (
                "recovery_release_gate_valid"
                if release_epoch
                else "recovery_release_legacy_completed"
            ),
            source_task_id=source_task_id,
            plan_id=plan_id,
            release_epoch=release_epoch or None,
        )

    @contextlib.contextmanager
    def dispatch_guard(
        self,
        task_id: str,
        *,
        app: Any | None = None,
        allow_terminal_task: bool = False,
    ) -> Iterator[RecoveryDispatchGateDecision]:
        """Hold the source mutation fence through claim/assignment commit."""

        repos = self._repos(app)
        task = repos.task_repo.get_by_id(str(task_id or ""))
        if not self._is_recovery_child(task):
            yield self.evaluate_task(
                task,
                app=app,
                repos=repos,
                allow_terminal_task=allow_terminal_task,
            )
            return
        source_task_id = str(
            _value(task, "source_task_id") or ""
        ).strip()
        if not source_task_id:
            yield self.evaluate_task(
                task,
                app=app,
                repos=repos,
                allow_terminal_task=allow_terminal_task,
            )
            return
        dependency_ids = {
            str(value).strip()
            for value in list(
                _value(task, "depends_on") or []
            )
            if str(value).strip()
        }
        with self._lock_port().mutation_locks(
            {
                source_task_id,
                str(task_id or ""),
                *dependency_ids,
            }
        ) as acquired:
            if not acquired:
                yield RecoveryDispatchGateDecision(
                    False,
                    "recovery_source_lock_unavailable",
                    source_task_id=source_task_id,
                    plan_id=str(
                        _value(task, "plan_id") or ""
                    )
                    or None,
                )
                return
            authoritative_task = repos.task_repo.get_by_id(
                str(task_id or "")
            )
            yield self.evaluate_task(
                authoritative_task,
                app=app,
                repos=repos,
                allow_terminal_task=allow_terminal_task,
            )

    def acquire_dispatch_lease(
        self,
        task_id: str,
        *,
        phase: str,
        worker_url: str | None = None,
        request_fingerprint: str | None = None,
        ttl_seconds: float = 600.0,
        app: Any | None = None,
    ) -> RecoveryDispatchLease:
        """Persist a short-lived capability without holding a DB lock over HTTP."""

        normalized_phase = self._normalize_phase(phase)
        if not normalized_phase:
            return RecoveryDispatchLease(
                RecoveryDispatchGateDecision(
                    False,
                    "recovery_dispatch_phase_invalid",
                ),
                phase=str(phase or ""),
            )
        repos = self._repos(app)
        with self.dispatch_guard(task_id, app=app) as decision:
            if not decision.allowed:
                return RecoveryDispatchLease(decision, phase=normalized_phase)
            task = repos.task_repo.get_by_id(str(task_id or ""))
            if not self._is_recovery_child(task):
                return RecoveryDispatchLease(decision, phase=normalized_phase)
            normalized_fingerprint = str(
                request_fingerprint or ""
            ).strip()
            if not normalized_fingerprint:
                return RecoveryDispatchLease(
                    RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_request_fingerprint_required",
                        source_task_id=decision.source_task_id,
                        plan_id=decision.plan_id,
                        release_epoch=decision.release_epoch,
                    ),
                    phase=normalized_phase,
                )
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    return RecoveryDispatchLease(
                        RecoveryDispatchGateDecision(
                            False,
                            "recovery_dispatch_task_lock_unavailable",
                            source_task_id=decision.source_task_id,
                            plan_id=decision.plan_id,
                            release_epoch=decision.release_epoch,
                        ),
                        phase=normalized_phase,
                    )
                authoritative = repos.task_repo.get_by_id(
                    str(task_id or "")
                )
                refreshed = self.evaluate_task(
                    authoritative,
                    app=app,
                    repos=repos,
                )
                if not refreshed.allowed:
                    return RecoveryDispatchLease(
                        refreshed,
                        phase=normalized_phase,
                    )
                token = secrets.token_urlsafe(32)
                now = time.time()
                expires_at = now + max(
                    15.0,
                    min(float(ttl_seconds or 600.0), 1800.0),
                )
                details = _mapping(
                    _value(authoritative, "status_reason_details")
                )
                previous = _mapping(
                    details.get("recovery_dispatch_lease")
                )
                if (
                    str(previous.get("state") or "")
                    in {"active", "worker_admitted"}
                    and float(previous.get("expires_at") or 0.0)
                    > now
                ):
                    return RecoveryDispatchLease(
                        RecoveryDispatchGateDecision(
                            False,
                            "recovery_dispatch_inflight",
                            source_task_id=refreshed.source_task_id,
                            plan_id=refreshed.plan_id,
                            release_epoch=refreshed.release_epoch,
                        ),
                        phase=normalized_phase,
                    )
                dispatch_lease = {
                    "schema": "ananta.recovery_dispatch_lease.v1",
                    "task_id": str(task_id or ""),
                    "token_digest": self._token_digest(token),
                    "phase": normalized_phase,
                    "state": "active",
                    "revision": int(previous.get("revision") or 0) + 1,
                    "issued_at": now,
                    "expires_at": expires_at,
                    "worker_url": str(worker_url or "").strip() or None,
                    "source_task_id": refreshed.source_task_id,
                    "plan_id": refreshed.plan_id,
                    "release_epoch": refreshed.release_epoch,
                    "request_fingerprint": normalized_fingerprint,
                }
                details["recovery_dispatch_lease"] = dispatch_lease
                if normalized_phase in {"propose", "execute"}:
                    from agent.services.recovery_hub_run_evidence_service import (
                        get_recovery_hub_run_evidence_service,
                    )

                    details = (
                        get_recovery_hub_run_evidence_service()
                        .prepare_for_dispatch_lease(
                            task_id=str(task_id or ""),
                            details=details,
                            phase=normalized_phase,
                            lease=dispatch_lease,
                        )
                    )
                setattr(authoritative, "status_reason_details", details)
                if hasattr(authoritative, "updated_at"):
                    setattr(authoritative, "updated_at", now)
                repos.task_repo.save(authoritative)
                return RecoveryDispatchLease(
                    refreshed,
                    phase=normalized_phase,
                    token=token,
                    expires_at=expires_at,
                )

    def reserve_run_evidence_context(
        self,
        task_id: str,
        *,
        worker_url: str,
        replace: bool,
        app: Any | None = None,
    ) -> dict[str, Any] | None:
        """Persist Worker-visible RUN authority before fingerprinting."""

        repos = self._repos(app)
        initial = repos.task_repo.get_by_id(str(task_id or ""))
        if not self._is_recovery_child(initial):
            return None
        with self.dispatch_guard(task_id, app=app) as decision:
            if not decision.allowed:
                raise RuntimeError(decision.reason_code)
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    raise RuntimeError(
                        "recovery_dispatch_task_lock_unavailable"
                    )
                authoritative = repos.task_repo.get_by_id(
                    str(task_id or "")
                )
                refreshed = self.evaluate_task(
                    authoritative,
                    app=app,
                    repos=repos,
                )
                if not refreshed.allowed:
                    raise RuntimeError(refreshed.reason_code)
                current_details = _mapping(
                    _value(
                        authoritative,
                        "status_reason_details",
                    )
                )
                current_lease = _mapping(
                    current_details.get(
                        "recovery_dispatch_lease"
                    )
                )
                try:
                    current_lease_expires_at = float(
                        current_lease.get("expires_at") or 0.0
                    )
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "recovery_dispatch_lease_invalid"
                    ) from exc
                if (
                    str(current_lease.get("state") or "")
                    in {"active", "worker_admitted"}
                    and current_lease_expires_at > time.time()
                ):
                    # Do not replace authority carried by an in-flight
                    # request; its eventual result must retain the exact
                    # record/lease/fingerprint binding.
                    raise RuntimeError(
                        "recovery_dispatch_inflight"
                    )
                from agent.services.recovery_hub_run_evidence_service import (
                    get_recovery_hub_run_evidence_service,
                )

                details = (
                    get_recovery_hub_run_evidence_service()
                        .reserve_context(
                        task_id=str(task_id or ""),
                        details=current_details,
                        worker_url=worker_url,
                        replace=replace,
                    )
                )
                setattr(
                    authoritative,
                    "status_reason_details",
                    details,
                )
                if hasattr(authoritative, "updated_at"):
                    setattr(
                        authoritative,
                        "updated_at",
                        time.time(),
                    )
                repos.task_repo.save(authoritative)
                return copy.deepcopy(
                    _mapping(
                        details.get(
                            "recovery_tool_run_context"
                        )
                    )
                )

    def validate_dispatch_lease(
        self,
        task_id: str,
        *,
        token: str | None,
        phase: str,
        request_fingerprint: str | None = None,
        app: Any | None = None,
    ) -> RecoveryDispatchGateDecision:
        """Revalidate the authoritative release and its opaque transport token."""

        normalized_phase = self._normalize_phase(phase)
        repos = self._repos(app)
        with self.dispatch_guard(task_id, app=app) as decision:
            if not decision.allowed:
                return decision
            task = repos.task_repo.get_by_id(str(task_id or ""))
            if not self._is_recovery_child(task):
                return decision
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    return RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_task_lock_unavailable",
                        source_task_id=decision.source_task_id,
                        plan_id=decision.plan_id,
                        release_epoch=decision.release_epoch,
                    )
                authoritative = repos.task_repo.get_by_id(
                    str(task_id or "")
                )
                refreshed = self.evaluate_task(
                    authoritative,
                    app=app,
                    repos=repos,
                )
                if not refreshed.allowed:
                    return refreshed
                return self._evaluate_lease_binding(
                    authoritative,
                    token=token,
                    phase=normalized_phase,
                    decision=refreshed,
                    allowed_states={"active"},
                    request_fingerprint=request_fingerprint,
                )

    def admit_dispatch_lease(
        self,
        task_id: str,
        *,
        token: str | None,
        phase: str,
        worker_url: str | None,
        request_fingerprint: str | None = None,
        worker_token: str | None = None,
        trusted_local: bool = False,
        app: Any | None = None,
    ) -> RecoveryDispatchGateDecision:
        """Atomically consume a lease for exactly one authenticated Worker call."""

        normalized_phase = self._normalize_phase(phase)
        normalized_worker_url = str(worker_url or "").strip().rstrip("/")
        repos = self._repos(app)
        with self.dispatch_guard(task_id, app=app) as decision:
            if not decision.allowed:
                return decision
            task = repos.task_repo.get_by_id(str(task_id or ""))
            if not self._is_recovery_child(task):
                return decision
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    return RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_task_lock_unavailable",
                        source_task_id=decision.source_task_id,
                        plan_id=decision.plan_id,
                        release_epoch=decision.release_epoch,
                    )
                authoritative = repos.task_repo.get_by_id(
                    str(task_id or "")
                )
                refreshed = self.evaluate_task(
                    authoritative,
                    app=app,
                    repos=repos,
                )
                if not refreshed.allowed:
                    return refreshed
                details = _mapping(
                    _value(authoritative, "status_reason_details")
                )
                lease = _mapping(
                    details.get("recovery_dispatch_lease")
                )
                lease_state = str(
                    lease.get("state") or ""
                )
                bound = self._evaluate_lease_binding(
                    authoritative,
                    token=token,
                    phase=normalized_phase,
                    decision=refreshed,
                    allowed_states=(
                        {"worker_admitted"}
                        if lease_state == "worker_admitted"
                        else {"active"}
                    ),
                    worker_url=normalized_worker_url,
                    request_fingerprint=request_fingerprint,
                )
                if not bound.allowed:
                    return bound
                if not trusted_local and not self._worker_identity_valid(
                    repos,
                    task=authoritative,
                    worker_url=normalized_worker_url,
                    worker_token=worker_token,
                    app=app,
                ):
                    return RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_worker_identity_denied",
                        source_task_id=refreshed.source_task_id,
                        plan_id=refreshed.plan_id,
                        release_epoch=refreshed.release_epoch,
                    )
                if lease_state == "worker_admitted":
                    return RecoveryDispatchGateDecision(
                        True,
                        "recovery_dispatch_worker_readmitted",
                        source_task_id=refreshed.source_task_id,
                        plan_id=refreshed.plan_id,
                        release_epoch=refreshed.release_epoch,
                    )
                lease["state"] = "worker_admitted"
                lease["admitted_at"] = time.time()
                lease["admitted_worker_url"] = normalized_worker_url
                details["recovery_dispatch_lease"] = lease
                setattr(authoritative, "status_reason_details", details)
                repos.task_repo.save(authoritative)
                return RecoveryDispatchGateDecision(
                    True,
                    "recovery_dispatch_worker_admitted",
                    source_task_id=refreshed.source_task_id,
                    plan_id=refreshed.plan_id,
                    release_epoch=refreshed.release_epoch,
                )

    @contextlib.contextmanager
    def result_guard(
        self,
        task_id: str,
        *,
        token: str | None,
        phase: str,
        request_fingerprint: str | None = None,
        worker_url: str | None = None,
        app: Any | None = None,
    ) -> Iterator[RecoveryDispatchGateDecision]:
        """Fence authoritative result writes and consume the matching lease."""

        normalized_phase = self._normalize_phase(phase)
        repos = self._repos(app)
        result_accepted = False
        accepted_status_transition: tuple[str, str] | None = None
        with self.dispatch_guard(
            task_id,
            app=app,
            allow_terminal_task=True,
        ) as decision:
            task = repos.task_repo.get_by_id(str(task_id or ""))
            if not decision.allowed or not self._is_recovery_child(task):
                yield decision
                return
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    yield RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_task_lock_unavailable",
                        source_task_id=decision.source_task_id,
                        plan_id=decision.plan_id,
                        release_epoch=decision.release_epoch,
                    )
                    return
                authoritative = repos.task_repo.get_by_id(
                    str(task_id or "")
                )
                refreshed = self.evaluate_task(
                    authoritative,
                    app=app,
                    repos=repos,
                    allow_terminal_task=True,
                )
                task_status = str(
                    _value(authoritative, "status") or ""
                ).strip().lower()
                if task_status in {
                    "cancelled",
                    "aborted",
                    "timeout",
                    "archived",
                    "skipped",
                }:
                    refreshed = RecoveryDispatchGateDecision(
                        False,
                        "recovery_dispatch_task_terminal",
                        source_task_id=refreshed.source_task_id,
                        plan_id=refreshed.plan_id,
                        release_epoch=refreshed.release_epoch,
                    )
                bound = (
                    self._evaluate_lease_binding(
                        authoritative,
                        token=token,
                        phase=normalized_phase,
                        decision=refreshed,
                        allowed_states={"worker_admitted"},
                        worker_url=(
                            str(worker_url or "").strip().rstrip("/")
                            if worker_url is not None
                            else None
                        ),
                        request_fingerprint=request_fingerprint,
                    )
                    if refreshed.allowed
                    else refreshed
                )
                if not bound.allowed:
                    yield bound
                    return
                try:
                    yield bound
                except BaseException:
                    raise
                else:
                    latest = repos.task_repo.get_by_id(
                        str(task_id or "")
                    )
                    exit_binding = self._evaluate_lease_binding(
                        latest,
                        token=token,
                        phase=normalized_phase,
                        decision=bound,
                        allowed_states={"worker_admitted"},
                        worker_url=(
                            str(worker_url or "").strip().rstrip("/")
                            if worker_url is not None
                            else None
                        ),
                        request_fingerprint=request_fingerprint,
                    )
                    if not exit_binding.allowed:
                        raise RuntimeError(
                            "recovery_result_lease_changed_before_commit:"
                            + exit_binding.reason_code
                        )

                    committed = _task_copy(latest)
                    old_status = str(
                        _value(latest, "status") or ""
                    ).strip().lower()
                    accepted_status = old_status
                    if normalized_phase == "execute":
                        accepted_status = (
                            self._validated_result_candidate(
                                committed,
                                phase=normalized_phase,
                            )
                        )
                        setattr(committed, "status", accepted_status)
                        if (
                            accepted_status == "verification_failed"
                            and hasattr(
                                committed,
                                "status_reason_code",
                            )
                        ):
                            setattr(
                                committed,
                                "status_reason_code",
                                (
                                    "recovery_result_"
                                    "verification_failed"
                                ),
                            )

                    committed_details = _mapping(
                        _value(committed, "status_reason_details")
                    )
                    committed_lease = _mapping(
                        committed_details.get(
                            "recovery_dispatch_lease"
                        )
                    )
                    committed_lease["state"] = "result_accepted"
                    committed_lease["accepted_at"] = time.time()
                    committed_lease["accepted_result_phase"] = (
                        normalized_phase
                    )
                    committed_lease["accepted_result_status"] = (
                        accepted_status
                    )
                    committed_lease["accepted_result_terminal"] = (
                        accepted_status in _TERMINAL_TASK_STATUSES
                    )
                    if normalized_phase == "execute":
                        result_candidate = _mapping(
                            committed_details.get(
                                "recovery_result_candidate"
                            )
                        )
                        result_candidate["state"] = "accepted"
                        result_candidate["accepted_at"] = (
                            committed_lease["accepted_at"]
                        )
                        committed_details[
                            "recovery_result_candidate"
                        ] = result_candidate
                    committed_details[
                        "recovery_dispatch_lease"
                    ] = committed_lease
                    setattr(
                        committed,
                        "status_reason_details",
                        committed_details,
                    )
                    if hasattr(committed, "updated_at"):
                        setattr(committed, "updated_at", time.time())
                    if normalized_phase == "execute":
                        from agent.services.task_runtime_service import (
                            append_task_history_event,
                        )

                        append_task_history_event(
                            committed,
                            event_type="recovery_result_committed",
                            actor="hub_recovery_dispatch_gate",
                            details={
                                "phase": normalized_phase,
                                "status": accepted_status,
                            },
                        )
                    committed_lease["accepted_result_digest"] = (
                        recovery_accepted_result_digest(committed)
                    )
                    commit_authority = contextlib.nullcontext()
                    if normalized_phase == "execute":
                        from agent.common.recovery_result_commit_write_boundary import (
                            authorize_recovery_result_commit_write,
                        )

                        commit_authority = (
                            authorize_recovery_result_commit_write(
                                task_id=str(task_id or ""),
                                lease=committed_lease,
                            )
                        )
                    with commit_authority:
                        persisted = repos.task_repo.save(
                            committed
                        )
                    persisted = (
                        persisted
                        or repos.task_repo.get_by_id(
                            str(task_id or "")
                        )
                    )
                    persisted_details = _mapping(
                        _value(
                            persisted,
                            "status_reason_details",
                        )
                    )
                    persisted_lease = _mapping(
                        persisted_details.get(
                            "recovery_dispatch_lease"
                        )
                    )
                    persisted_status = str(
                        _value(persisted, "status") or ""
                    ).strip().lower()
                    if (
                        str(persisted_lease.get("state") or "")
                        != "result_accepted"
                        or str(
                            persisted_lease.get(
                                "accepted_result_phase"
                            )
                            or ""
                        )
                        != normalized_phase
                        or str(
                            persisted_lease.get(
                                "accepted_result_status"
                            )
                            or ""
                        )
                        != accepted_status
                        or persisted_lease.get(
                            "accepted_result_terminal"
                        )
                        is not (
                            accepted_status
                            in _TERMINAL_TASK_STATUSES
                        )
                        or not hmac.compare_digest(
                            str(
                                persisted_lease.get(
                                    "accepted_result_digest"
                                )
                                or ""
                            ),
                            str(
                                committed_lease.get(
                                    "accepted_result_digest"
                                )
                                or ""
                            ),
                        )
                        or (
                            normalized_phase == "execute"
                            and (
                                persisted_status
                                != accepted_status
                                or not hmac.compare_digest(
                                    str(
                                        persisted_lease.get(
                                            "accepted_result_digest"
                                        )
                                        or ""
                                    ),
                                    recovery_accepted_result_digest(
                                        persisted
                                    ),
                                )
                            )
                        )
                    ):
                        raise RuntimeError(
                            "recovery_result_commit_rejected"
                        )
                    result_accepted = True
                    accepted_status_transition = (
                        old_status,
                        accepted_status,
                    )
        if (
            result_accepted
            and accepted_status_transition is not None
            and accepted_status_transition[0]
            != accepted_status_transition[1]
            and self._repository_provider is None
        ):
            from agent.services.task_runtime_service import (
                run_external_task_status_post_commit,
            )

            try:
                run_external_task_status_post_commit(
                    str(task_id or ""),
                    old_status=accepted_status_transition[0],
                    event_type="recovery_result_committed",
                    force=True,
                )
            except Exception:
                _LOG.exception(
                    "Recovery result post-commit failed for %s",
                    task_id,
                )
        if result_accepted:
            from agent.services.autopilot_wake_service import (
                request_autopilot_wake,
            )

            request_autopilot_wake(
                "recovery_result_accepted",
                task_id=str(task_id or ""),
                phase=normalized_phase,
            )

    def admit_incoming_dispatch(
        self,
        *,
        task: Any,
        token: str | None,
        phase: str,
        request_fingerprint: str | None = None,
        timeout_seconds: float = 3.0,
    ) -> RecoveryDispatchGateDecision:
        """Worker-side admission backed by the Hub's authoritative lease."""

        recovery_child = self._is_recovery_child(task)
        if token and not recovery_child:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_unexpected",
            )
        if not recovery_child:
            return RecoveryDispatchGateDecision(
                True,
                "not_recovery_child",
            )
        task_id = str(_value(task, "id") or "").strip()
        if not task_id or not token:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_missing",
            )
        from agent.config import settings

        if str(settings.role or "").strip().lower() == "hub":
            local_url = str(
                settings.agent_url
                or f"http://localhost:{settings.port}"
            ).strip().rstrip("/")
            return self.admit_dispatch_lease(
                task_id,
                token=token,
                phase=phase,
                worker_url=local_url,
                request_fingerprint=request_fingerprint,
                trusted_local=True,
            )

        hub_url = str(settings.hub_url or "").strip().rstrip("/")
        worker_url = str(
            settings.agent_url
            or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        if not hub_url:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_hub_unavailable",
            )
        try:
            import requests

            from agent.auth import resolve_configured_agent_token

            worker_token = resolve_configured_agent_token()
            if not worker_token:
                return RecoveryDispatchGateDecision(
                    False,
                    "recovery_dispatch_worker_identity_denied",
                )
            response = requests.post(
                (
                    f"{hub_url}/internal/tasks/{task_id}"
                    "/recovery-dispatch-admission"
                ),
                json={
                    "phase": self._normalize_phase(phase),
                    "request_fingerprint": str(
                        request_fingerprint or ""
                    ),
                },
                headers={
                    "Authorization": f"Bearer {worker_token}",
                    "X-Ananta-Recovery-Dispatch-Lease": str(token),
                    "X-Ananta-Worker-Url": worker_url,
                },
                timeout=max(0.5, min(float(timeout_seconds), 10.0)),
            )
            if int(response.status_code) >= 400:
                return RecoveryDispatchGateDecision(
                    False,
                    "recovery_dispatch_hub_rejected",
                )
            body = response.json()
            payload = (
                body.get("data")
                if isinstance(body, Mapping)
                else None
            )
            if not isinstance(payload, Mapping) or not bool(
                payload.get("allowed")
            ):
                return RecoveryDispatchGateDecision(
                    False,
                    str(
                        (payload or {}).get("reason_code")
                        or "recovery_dispatch_hub_rejected"
                    ),
                )
            return RecoveryDispatchGateDecision(
                True,
                str(
                    payload.get("reason_code")
                    or "recovery_dispatch_lease_valid"
                ),
                source_task_id=(
                    str(payload.get("source_task_id") or "") or None
                ),
                plan_id=str(payload.get("plan_id") or "") or None,
                release_epoch=(
                    str(payload.get("release_epoch") or "") or None
                ),
            )
        except Exception:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_hub_unavailable",
            )

    @staticmethod
    def _normalize_phase(phase: str | None) -> str:
        normalized = str(phase or "").strip().lower()
        return normalized if normalized in {
            "propose",
            "execute",
            "delegate",
        } else ""

    @staticmethod
    def _token_digest(token: str | None) -> str:
        return hashlib.sha256(
            str(token or "").encode("utf-8")
        ).hexdigest()

    def _evaluate_lease_binding(
        self,
        task: Any,
        *,
        token: str | None,
        phase: str,
        decision: RecoveryDispatchGateDecision,
        allowed_states: set[str],
        worker_url: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RecoveryDispatchGateDecision:
        lease = _mapping(
            _mapping(
                _value(task, "status_reason_details")
            ).get("recovery_dispatch_lease")
        )
        token_digest = str(lease.get("token_digest") or "")
        if not token or not token_digest or not hmac.compare_digest(
            token_digest,
            self._token_digest(token),
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_mismatch",
                source_task_id=decision.source_task_id,
                plan_id=decision.plan_id,
                release_epoch=decision.release_epoch,
            )
        if (
            str(lease.get("schema") or "")
            != "ananta.recovery_dispatch_lease.v1"
            or str(lease.get("state") or "") not in allowed_states
            or str(lease.get("phase") or "") != phase
            or float(lease.get("expires_at") or 0.0) <= time.time()
            or str(lease.get("source_task_id") or "")
            != str(decision.source_task_id or "")
            or str(lease.get("plan_id") or "")
            != str(decision.plan_id or "")
            or str(lease.get("release_epoch") or "")
            != str(decision.release_epoch or "")
            or not request_fingerprint
            or not hmac.compare_digest(
                str(lease.get("request_fingerprint") or ""),
                str(request_fingerprint or ""),
            )
            or (
                worker_url is not None
                and str(lease.get("worker_url") or "").rstrip("/")
                != str(worker_url or "").rstrip("/")
            )
        ):
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_inactive",
                source_task_id=decision.source_task_id,
                plan_id=decision.plan_id,
                release_epoch=decision.release_epoch,
            )
        return RecoveryDispatchGateDecision(
            True,
            "recovery_dispatch_lease_valid",
            source_task_id=decision.source_task_id,
            plan_id=decision.plan_id,
            release_epoch=decision.release_epoch,
        )

    @staticmethod
    def _worker_identity_valid(
        repos: Any,
        *,
        task: Any,
        worker_url: str,
        worker_token: str | None,
        app: Any | None = None,
    ) -> bool:
        if not worker_url or not worker_token:
            return False
        try:
            worker = repos.agent_repo.get_by_url(worker_url)
            agents = tuple(repos.agent_repo.get_all() or ())
        except Exception:
            return False
        if worker is None:
            return False
        try:
            from flask import current_app, has_app_context

            from agent.auth import resolve_configured_agent_token
            from agent.services.workflow_worker_service_auth import (
                RECOVERY_TASK_DISPATCH_SCOPE,
                authenticate_registered_workflow_worker,
            )

            config = (
                getattr(app, "config", None)
                if app is not None
                else current_app.config if has_app_context() else None
            )
            hub_service_token = resolve_configured_agent_token(
                config
            )
            user_session_secret = (
                getattr(app, "secret_key", None)
                if app is not None
                else current_app.secret_key
                if has_app_context()
                else None
            )
            identity = authenticate_registered_workflow_worker(
                str(worker_token),
                required_scope=RECOVERY_TASK_DISPATCH_SCOPE,
                claimed_worker_id=str(
                    _value(worker, "name") or ""
                ),
                claimed_worker_url=str(worker_url),
                registered_agents=agents,
                hub_service_token=hub_service_token,
                user_session_secret=user_session_secret,
                config=config,
            )
        except Exception:
            return False
        required_capabilities = {
            str(value).strip()
            for value in (
                _value(task, "required_capabilities") or ()
            )
            if str(value).strip()
        }
        return required_capabilities.issubset(
            set(identity.capabilities)
        )

    def invalidate_task(
        self,
        task_id: str,
        *,
        reason_code: str,
    ) -> bool:
        normalized_task_id = str(task_id or "").strip()
        normalized_reason = str(reason_code or "").strip()[:160]
        if not normalized_task_id or not normalized_reason:
            return False
        repos = self._repos()
        task = repos.task_repo.get_by_id(normalized_task_id)
        if not self._is_recovery_child(task):
            return False
        details = _mapping(
            _value(task, "status_reason_details")
        )
        lease = _mapping(details.get("recovery_dispatch_lease"))
        if str(lease.get("state") or "") in {
            "active",
            "worker_admitted",
        }:
            return (
                self.abort_dispatch_lease(
                    normalized_task_id,
                    target_status="cancelled",
                    reason_code=normalized_reason,
                    error=normalized_reason,
                )
                == "cancelled"
            )
        previous_status = str(
            _value(task, "status") or ""
        ).strip().lower()
        if (
            not previous_status
            or previous_status in _TERMINAL_TASK_STATUSES
        ):
            return False
        cancelled_at = time.time()
        marker = {
            "schema": "ananta.recovery_child_cancellation.v1",
            "task_id": normalized_task_id,
            "source_task_id": str(
                _value(task, "source_task_id") or ""
            ).strip(),
            "goal_id": str(
                _value(task, "goal_id") or ""
            ).strip(),
            "plan_id": str(
                _value(task, "plan_id") or ""
            ).strip(),
            "previous_status": previous_status,
            "target_status": "cancelled",
            "reason_code": normalized_reason,
            "cancelled_at": cancelled_at,
        }
        details["recovery_child_cancellation"] = marker
        from agent.common.recovery_child_cancellation_write_boundary import (
            authorize_recovery_child_cancellation_write,
        )
        from agent.services.task_runtime_service import (
            compare_and_set_local_task_status,
        )

        with authorize_recovery_child_cancellation_write(
            task_id=normalized_task_id,
            marker=marker,
        ):
            return compare_and_set_local_task_status(
                normalized_task_id,
                "cancelled",
                expected_statuses={previous_status},
                event_type="recovery_dispatch_gate_invalidated",
                event_actor="hub_dispatch_gate",
                event_details={"reason_code": normalized_reason},
                status_reason_code=normalized_reason,
                status_reason_details=details,
                force=True,
            )

    def revoke_dispatch_lease(
        self,
        task_id: str,
        *,
        reason_code: str,
        app: Any | None = None,
    ) -> bool:
        """Revoke an in-flight Recovery capability under owner locks."""

        repos = self._repos(app)
        task = repos.task_repo.get_by_id(str(task_id or ""))
        if not self._is_recovery_child(task):
            return False
        source_task_id = str(
            _value(task, "source_task_id") or ""
        ).strip()
        lock_ids = {str(task_id or "")}
        if source_task_id:
            lock_ids.add(source_task_id)
        with self._lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                return False
            authoritative = repos.task_repo.get_by_id(
                str(task_id or "")
            )
            if authoritative is None:
                return False
            details = _mapping(
                _value(authoritative, "status_reason_details")
            )
            lease = _mapping(
                details.get("recovery_dispatch_lease")
            )
            if (
                not lease
                or str(lease.get("state") or "")
                not in {"active", "worker_admitted"}
            ):
                return False
            if self._accepted_terminal_result_is_proven(
                authoritative,
                lease,
            ):
                return False
            try:
                expected_revision = int(
                    lease.get("revision")
                ) + 1
            except (TypeError, ValueError):
                return False
            normalized_reason = str(reason_code or "")[:160]
            if not normalized_reason:
                return False
            previous_lease = dict(lease)
            lease.update(
                {
                    "state": "revoked",
                    "revoked_at": time.time(),
                    "revocation_reason": normalized_reason,
                    "revision": expected_revision,
                }
            )
            details["recovery_dispatch_lease"] = lease
            setattr(
                authoritative,
                "status_reason_details",
                details,
            )
            from agent.common.recovery_dispatch_invalidation_write_boundary import (
                authorize_recovery_dispatch_invalidation_write,
            )

            with authorize_recovery_dispatch_invalidation_write(
                task_id=str(task_id or ""),
                current_lease=previous_lease,
                proposed_lease=lease,
            ):
                persisted = (
                    repos.task_repo.save(authoritative)
                    or repos.task_repo.get_by_id(
                        str(task_id or "")
                    )
                )
            persisted_lease = _mapping(
                _mapping(
                    _value(
                        persisted,
                        "status_reason_details",
                    )
                ).get("recovery_dispatch_lease")
            )
            try:
                persisted_revision = int(
                    persisted_lease.get("revision") or 0
                )
            except (TypeError, ValueError):
                return False
            return bool(
                str(persisted_lease.get("state") or "")
                == "revoked"
                and persisted_revision == expected_revision
                and str(
                    persisted_lease.get(
                        "revocation_reason"
                    )
                    or ""
                )
                == normalized_reason
            )

    def abort_dispatch_lease(
        self,
        task_id: str,
        *,
        target_status: str,
        reason_code: str,
        error: str,
        app: Any | None = None,
    ) -> str:
        """Atomically let an accepted terminal result or an abort win."""

        repos = self._repos(app)
        task = repos.task_repo.get_by_id(str(task_id or ""))
        if not self._is_recovery_child(task):
            return ""
        source_task_id = str(
            _value(task, "source_task_id") or ""
        ).strip()
        lock_ids = {str(task_id or "")}
        if source_task_id:
            lock_ids.add(source_task_id)
        status_transition: tuple[str, str] | None = None
        final_status = ""
        with self._lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                return ""
            authoritative = repos.task_repo.get_by_id(
                str(task_id or "")
            )
            current_status = str(
                _value(authoritative, "status") or ""
            ).strip().lower()
            details = _mapping(
                _value(authoritative, "status_reason_details")
            )
            lease = _mapping(
                details.get("recovery_dispatch_lease")
            )
            if (
                current_status in _TERMINAL_TASK_STATUSES
                and self._accepted_terminal_result_is_proven(
                    authoritative,
                    lease,
                )
            ):
                return current_status

            inconsistent_terminal = (
                current_status in _TERMINAL_TASK_STATUSES
            )
            final_status = (
                "verification_failed"
                if current_status == "completed"
                else current_status
                if inconsistent_terminal
                else str(target_status or "").strip().lower()
            )
            committed = _task_copy(authoritative)
            committed_details = _mapping(
                _value(committed, "status_reason_details")
            )
            committed_lease = _mapping(
                committed_details.get("recovery_dispatch_lease")
            )
            if committed_lease:
                committed_lease.update(
                    {
                        "state": "revoked",
                        "revoked_at": time.time(),
                        "revocation_reason": (
                            "recovery_terminal_without_accepted_result"
                            if inconsistent_terminal
                            else str(reason_code or "")[:160]
                        ),
                        "revision": int(
                            committed_lease.get("revision") or 0
                        )
                        + 1,
                    }
                )
                committed_details[
                    "recovery_dispatch_lease"
                ] = committed_lease
            setattr(
                committed,
                "status_reason_details",
                committed_details,
            )
            setattr(committed, "status", final_status)
            if hasattr(committed, "error"):
                setattr(committed, "error", str(error or ""))
            if hasattr(committed, "status_reason_code"):
                setattr(
                    committed,
                    "status_reason_code",
                    (
                        "recovery_result_verification_failed"
                        if inconsistent_terminal
                        else str(reason_code or "")[:160]
                    ),
                )
            if hasattr(committed, "updated_at"):
                setattr(committed, "updated_at", time.time())
            from agent.services.task_runtime_service import (
                append_task_history_event,
            )

            append_task_history_event(
                committed,
                event_type=(
                    "recovery_result_acceptance_inconsistent"
                    if inconsistent_terminal
                    else "recovery_dispatch_aborted"
                ),
                actor="autopilot_tick",
                details={
                    "reason": str(error or ""),
                    "previous_status": current_status,
                    "lease_state": str(lease.get("state") or ""),
                },
            )
            requires_abort_authority = bool(
                final_status in _TERMINAL_TASK_STATUSES
                and committed_lease
                and str(committed_lease.get("state") or "")
                == "revoked"
            )
            from agent.common.recovery_dispatch_invalidation_write_boundary import (
                authorize_recovery_dispatch_invalidation_write,
            )

            with authorize_recovery_dispatch_invalidation_write(
                task_id=str(task_id or ""),
                current_lease=lease,
                proposed_lease=committed_lease,
            ):
                if requires_abort_authority:
                    from agent.common.recovery_dispatch_abort_write_boundary import (
                        authorize_recovery_dispatch_abort_write,
                    )

                    with authorize_recovery_dispatch_abort_write(
                        task_id=str(task_id or ""),
                        current_lease=lease,
                        proposed_lease=committed_lease,
                        target_status=final_status,
                    ):
                        persisted = repos.task_repo.save(
                            committed
                        )
                else:
                    persisted = repos.task_repo.save(committed)
            final_status = str(
                _value(persisted, "status") or final_status
            ).strip().lower()
            persisted_lease = _mapping(
                _mapping(
                    _value(
                        persisted,
                        "status_reason_details",
                    )
                ).get("recovery_dispatch_lease")
            )
            try:
                persisted_revision = int(
                    persisted_lease.get("revision") or -1
                )
                committed_revision = int(
                    committed_lease.get("revision") or -2
                )
            except (TypeError, ValueError):
                persisted_revision = -1
                committed_revision = -2
            if (
                final_status
                != str(
                    _value(committed, "status") or ""
                ).strip().lower()
                or str(persisted_lease.get("state") or "")
                != "revoked"
                or persisted_revision != committed_revision
                or str(
                    persisted_lease.get(
                        "revocation_reason"
                    )
                    or ""
                )
                != str(
                    committed_lease.get(
                        "revocation_reason"
                    )
                    or ""
                )
            ):
                raise RuntimeError(
                    "recovery_dispatch_abort_commit_rejected"
                )
            status_transition = (current_status, final_status)

        if (
            status_transition is not None
            and status_transition[0] != status_transition[1]
            and self._repository_provider is None
        ):
            from agent.services.task_runtime_service import (
                run_external_task_status_post_commit,
            )

            try:
                run_external_task_status_post_commit(
                    str(task_id or ""),
                    old_status=status_transition[0],
                    event_type=(
                        "recovery_result_acceptance_inconsistent"
                        if status_transition[0]
                        in _TERMINAL_TASK_STATUSES
                        else "recovery_dispatch_aborted"
                    ),
                    force=True,
                )
            except Exception:
                _LOG.exception(
                    "Recovery abort post-commit failed for %s",
                    task_id,
                )
        return final_status

_service = RecoveryDispatchGateService()


def get_recovery_dispatch_gate_service() -> (
    RecoveryDispatchGateService
):
    return _service
