"""Terminal settlement of Recovery dispatch leases.

Either an accepted Worker result or an abort/revocation wins a lease; this
module owns both fenced write paths and their post-commit notifications.
Lock acquisition order and the write-boundary authorizations are identical to
the former in-class implementation.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import time
from typing import Any, Callable, Protocol

from agent.common.recovery_dispatch_contract import (
    RecoveryDispatchGateDecision,
    _mapping,
    _value,
    recovery_accepted_result_digest,
)
from agent.common.recovery_dispatch_contract import (
    task_copy as _task_copy,
)
from agent.services.recovery_dispatch_gate_policy import (
    IN_FLIGHT_LEASE_STATES,
    TERMINAL_TASK_STATUSES,
    accepted_terminal_result_is_proven,
    is_recovery_child,
    validated_result_candidate,
)

_LOG = logging.getLogger("agent.services.recovery_dispatch_gate_service")


class LeaseBindingEvaluator(Protocol):
    def __call__(
        self,
        task: Any,
        *,
        token: str | None,
        phase: str,
        decision: RecoveryDispatchGateDecision,
        allowed_states: set[str],
        worker_url: str | None = None,
        request_fingerprint: str | None = None,
    ) -> RecoveryDispatchGateDecision: ...


class RecoveryDispatchLeaseSettlement:
    """Commit accepted results and abort/revoke in-flight leases."""

    def __init__(
        self,
        *,
        repos_resolver: Callable[..., Any],
        lock_port_resolver: Callable[[], Any],
        post_commit_enabled: Callable[[], bool],
    ) -> None:
        self._repos = repos_resolver
        self._lock_port = lock_port_resolver
        self._post_commit_enabled = post_commit_enabled

    # -- Accepted result -------------------------------------------------

    def commit_accepted_result(
        self,
        task_id: str,
        *,
        repos: Any,
        phase: str,
        token: str | None,
        bound: RecoveryDispatchGateDecision,
        worker_url: str | None,
        request_fingerprint: str | None,
        lease_binding: LeaseBindingEvaluator,
    ) -> tuple[str, str]:
        """Publish the fenced result; return ``(old_status, accepted_status)``.

        Must run under the task mutation lock held by ``result_guard``.
        """

        latest = repos.task_repo.get_by_id(
            str(task_id or "")
        )
        exit_binding = lease_binding(
            latest,
            token=token,
            phase=phase,
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
        if phase == "execute":
            accepted_status = validated_result_candidate(
                committed,
                phase=phase,
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

        committed_lease = self._stage_result_acceptance(
            committed,
            phase=phase,
            accepted_status=accepted_status,
        )
        commit_authority: Any = contextlib.nullcontext()
        if phase == "execute":
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
        if not self._result_commit_persisted(
            persisted,
            phase=phase,
            accepted_status=accepted_status,
            committed_lease=committed_lease,
        ):
            raise RuntimeError(
                "recovery_result_commit_rejected"
            )
        return old_status, accepted_status

    @staticmethod
    def _stage_result_acceptance(
        committed: Any,
        *,
        phase: str,
        accepted_status: str,
    ) -> dict[str, Any]:
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
            phase
        )
        committed_lease["accepted_result_status"] = (
            accepted_status
        )
        committed_lease["accepted_result_terminal"] = (
            accepted_status in TERMINAL_TASK_STATUSES
        )
        if phase == "execute":
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
        if phase == "execute":
            from agent.services.task_runtime_service import (
                append_task_history_event,
            )

            append_task_history_event(
                committed,
                event_type="recovery_result_committed",
                actor="hub_recovery_dispatch_gate",
                details={
                    "phase": phase,
                    "status": accepted_status,
                },
            )
        committed_lease["accepted_result_digest"] = (
            recovery_accepted_result_digest(committed)
        )
        return committed_lease

    @staticmethod
    def _result_commit_persisted(
        persisted: Any,
        *,
        phase: str,
        accepted_status: str,
        committed_lease: dict[str, Any],
    ) -> bool:
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
        return not (
            str(persisted_lease.get("state") or "")
            != "result_accepted"
            or str(
                persisted_lease.get(
                    "accepted_result_phase"
                )
                or ""
            )
            != phase
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
                in TERMINAL_TASK_STATUSES
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
                phase == "execute"
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
        )

    def notify_result_accepted(
        self,
        task_id: str,
        *,
        phase: str,
        status_transition: tuple[str, str] | None,
    ) -> None:
        """Run post-commit hooks after the result fence was released."""

        if (
            status_transition is not None
            and status_transition[0]
            != status_transition[1]
            and self._post_commit_enabled()
        ):
            from agent.services.task_runtime_service import (
                run_external_task_status_post_commit,
            )

            try:
                run_external_task_status_post_commit(
                    str(task_id or ""),
                    old_status=status_transition[0],
                    event_type="recovery_result_committed",
                    force=True,
                )
            except Exception:
                _LOG.exception(
                    "Recovery result post-commit failed for %s",
                    task_id,
                )
        from agent.services.autopilot_wake_service import (
            request_autopilot_wake,
        )

        request_autopilot_wake(
            "recovery_result_accepted",
            task_id=str(task_id or ""),
            phase=phase,
        )

    # -- Invalidation / revocation / abort ----------------------------------

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
        if not is_recovery_child(task):
            return False
        details = _mapping(
            _value(task, "status_reason_details")
        )
        lease = _mapping(details.get("recovery_dispatch_lease"))
        if str(lease.get("state") or "") in IN_FLIGHT_LEASE_STATES:
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
            or previous_status in TERMINAL_TASK_STATUSES
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

    def _owner_lock_ids(self, task: Any, task_id: str) -> set[str]:
        source_task_id = str(
            _value(task, "source_task_id") or ""
        ).strip()
        lock_ids = {str(task_id or "")}
        if source_task_id:
            lock_ids.add(source_task_id)
        return lock_ids

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
        if not is_recovery_child(task):
            return False
        lock_ids = self._owner_lock_ids(task, task_id)
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
                not in IN_FLIGHT_LEASE_STATES
            ):
                return False
            if accepted_terminal_result_is_proven(
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
        if not is_recovery_child(task):
            return ""
        lock_ids = self._owner_lock_ids(task, task_id)
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
                current_status in TERMINAL_TASK_STATUSES
                and accepted_terminal_result_is_proven(
                    authoritative,
                    lease,
                )
            ):
                return current_status

            inconsistent_terminal = (
                current_status in TERMINAL_TASK_STATUSES
            )
            final_status = (
                "verification_failed"
                if current_status == "completed"
                else current_status
                if inconsistent_terminal
                else str(target_status or "").strip().lower()
            )
            committed = _task_copy(authoritative)
            committed_lease = self._stage_abort(
                committed,
                final_status=final_status,
                inconsistent_terminal=inconsistent_terminal,
                reason_code=reason_code,
                error=error,
                current_status=current_status,
                lease=lease,
            )
            persisted = self._save_abort(
                repos,
                task_id,
                committed=committed,
                lease=lease,
                committed_lease=committed_lease,
                final_status=final_status,
            )
            final_status = str(
                _value(persisted, "status") or final_status
            ).strip().lower()
            if not self._abort_persisted(
                persisted,
                committed=committed,
                committed_lease=committed_lease,
                final_status=final_status,
            ):
                raise RuntimeError(
                    "recovery_dispatch_abort_commit_rejected"
                )
            status_transition = (current_status, final_status)

        self._notify_abort(task_id, status_transition)
        return final_status

    @staticmethod
    def _stage_abort(
        committed: Any,
        *,
        final_status: str,
        inconsistent_terminal: bool,
        reason_code: str,
        error: str,
        current_status: str,
        lease: dict[str, Any],
    ) -> dict[str, Any]:
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
        return committed_lease

    @staticmethod
    def _save_abort(
        repos: Any,
        task_id: str,
        *,
        committed: Any,
        lease: dict[str, Any],
        committed_lease: dict[str, Any],
        final_status: str,
    ) -> Any:
        requires_abort_authority = bool(
            final_status in TERMINAL_TASK_STATUSES
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
        return persisted

    @staticmethod
    def _abort_persisted(
        persisted: Any,
        *,
        committed: Any,
        committed_lease: dict[str, Any],
        final_status: str,
    ) -> bool:
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
        return not (
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
        )

    def _notify_abort(
        self,
        task_id: str,
        status_transition: tuple[str, str] | None,
    ) -> None:
        if (
            status_transition is not None
            and status_transition[0] != status_transition[1]
            and self._post_commit_enabled()
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
                        in TERMINAL_TASK_STATUSES
                        else "recovery_dispatch_aborted"
                    ),
                    force=True,
                )
            except Exception:
                _LOG.exception(
                    "Recovery abort post-commit failed for %s",
                    task_id,
                )
