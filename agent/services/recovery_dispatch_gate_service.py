"""Claim-time fence for Hub-materialized recovery tasks.

The service owns the lock-fenced lease lifecycle and composes the
``recovery_dispatch_gate_{policy,release_evaluation,lease_settlement,
worker_admission}`` collaborators.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import secrets
import time
from typing import Any, Callable, Iterator

from agent.common.recovery_dispatch_contract import (  # noqa: F401
    _RESULT_CANDIDATE_SCHEMA,
    RecoveryDispatchGateDecision,
    RecoveryDispatchLease,
    _mapping,
    _value,
)
from agent.common.recovery_dispatch_contract import (
    build_recovery_result_candidate as build_recovery_result_candidate,
)
from agent.common.recovery_dispatch_contract import (
    recovery_accepted_result_digest as recovery_accepted_result_digest,
)
from agent.common.recovery_dispatch_contract import (
    recovery_dispatch_request_fingerprint as recovery_dispatch_request_fingerprint,
)
from agent.common.recovery_dispatch_contract import (  # noqa: F401
    task_copy as _task_copy,
)
from agent.services import recovery_dispatch_gate_policy as _gate_policy
from agent.services.recovery_dispatch_gate_lease_settlement import (
    RecoveryDispatchLeaseSettlement,
)
from agent.services.recovery_dispatch_gate_policy import (  # noqa: F401
    DISPATCHABLE_RECOVERY_STATUSES as _DISPATCHABLE_RECOVERY_STATUSES,
)
from agent.services.recovery_dispatch_gate_policy import (
    IN_FLIGHT_LEASE_STATES,
    RECOVERY_DISPATCH_LEASE_SCHEMA,
    denied_like,
    evaluate_lease_binding,
)
from agent.services.recovery_dispatch_gate_policy import (  # noqa: F401
    SUCCESSFUL_DEPENDENCY_STATUSES as _SUCCESSFUL_DEPENDENCY_STATUSES,
)
from agent.services.recovery_dispatch_gate_policy import (  # noqa: F401
    TERMINAL_GOAL_STATUSES as _TERMINAL_GOAL_STATUSES,
)
from agent.services.recovery_dispatch_gate_policy import (  # noqa: F401
    TERMINAL_TASK_STATUSES as _TERMINAL_TASK_STATUSES,
)
from agent.services.recovery_dispatch_gate_release_evaluation import (
    evaluate_recovery_release,
)
from agent.services.recovery_dispatch_gate_worker_admission import (
    admit_incoming_recovery_dispatch,
)
from agent.services.recovery_dispatch_gate_worker_admission import (
    recovery_worker_identity_valid as _recovery_worker_identity_valid,
)
from agent.services.recovery_plan_contract import (  # noqa: F401
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)

_LOG = logging.getLogger(__name__)


class RecoveryDispatchGateService:
    """Validate persisted release ownership immediately before a Hub claim."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any] | None = None,
        mutation_lock_provider: Callable[[], Any] | None = None,
        lease_settlement: RecoveryDispatchLeaseSettlement | None = None,
    ) -> None:
        self._repository_provider = repository_provider
        self._mutation_lock_provider = mutation_lock_provider
        # The settlement resolves repositories and locks through this owner
        # at call time, so provider overrides after construction still apply.
        self._lease_settlement = lease_settlement or (
            RecoveryDispatchLeaseSettlement(
                repos_resolver=self._repos,
                lock_port_resolver=self._lock_port,
                post_commit_enabled=self._post_commit_enabled,
            )
        )

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

    def _post_commit_enabled(self) -> bool:
        # Injected repositories are test/in-memory stores: the external
        # status post-commit only runs against the production registry.
        return self._repository_provider is None

    # -- Pure predicates (kept as class members for compatibility) ---------

    _is_recovery_child = staticmethod(_gate_policy.is_recovery_child)
    _is_recovery_source = staticmethod(_gate_policy.is_recovery_source)
    _accepted_terminal_result_is_proven = staticmethod(
        _gate_policy.accepted_terminal_result_is_proven
    )
    _validated_result_candidate = staticmethod(
        _gate_policy.validated_result_candidate
    )
    _normalize_phase = staticmethod(_gate_policy.normalize_dispatch_phase)
    _token_digest = staticmethod(_gate_policy.dispatch_token_digest)
    _worker_identity_valid = staticmethod(_recovery_worker_identity_valid)

    @classmethod
    def is_recovery_child(cls, task: Any) -> bool:
        """Public predicate shared by Hub and Worker admission boundaries."""

        return cls._is_recovery_child(task)

    @classmethod
    def is_recovery_source(cls, task: Any) -> bool:
        return cls._is_recovery_source(task)

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
        return evaluate_lease_binding(
            task,
            token=token,
            phase=phase,
            decision=decision,
            allowed_states=allowed_states,
            worker_url=worker_url,
            request_fingerprint=request_fingerprint,
        )

    # -- Release evaluation and claim fence --------------------------------

    def evaluate_task(
        self,
        task: Any,
        *,
        app: Any | None = None,
        repos: Any | None = None,
        allow_terminal_task: bool = False,
    ) -> RecoveryDispatchGateDecision:
        return evaluate_recovery_release(
            task,
            resolve_repos=lambda: repos or self._repos(app),
            allow_terminal_task=allow_terminal_task,
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

    # -- Lease lifecycle ---------------------------------------------------

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
                    denied_like(
                        decision,
                        "recovery_dispatch_request_fingerprint_required",
                    ),
                    phase=normalized_phase,
                )
            with self._lock_port().mutation_lock(
                str(task_id or "")
            ) as acquired:
                if not acquired:
                    return RecoveryDispatchLease(
                        denied_like(
                            decision,
                            "recovery_dispatch_task_lock_unavailable",
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
                    in IN_FLIGHT_LEASE_STATES
                    and float(previous.get("expires_at") or 0.0)
                    > now
                ):
                    return RecoveryDispatchLease(
                        denied_like(
                            refreshed,
                            "recovery_dispatch_inflight",
                        ),
                        phase=normalized_phase,
                    )
                dispatch_lease = {
                    "schema": RECOVERY_DISPATCH_LEASE_SCHEMA,
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
                    in IN_FLIGHT_LEASE_STATES
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
                    return denied_like(
                        decision,
                        "recovery_dispatch_task_lock_unavailable",
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
                    return denied_like(
                        decision,
                        "recovery_dispatch_task_lock_unavailable",
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
                    return denied_like(
                        refreshed,
                        "recovery_dispatch_worker_identity_denied",
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
        normalized_worker_url = (
            str(worker_url or "").strip().rstrip("/")
            if worker_url is not None
            else None
        )
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
                    yield denied_like(
                        decision,
                        "recovery_dispatch_task_lock_unavailable",
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
                    refreshed = denied_like(
                        refreshed,
                        "recovery_dispatch_task_terminal",
                    )
                bound = (
                    self._evaluate_lease_binding(
                        authoritative,
                        token=token,
                        phase=normalized_phase,
                        decision=refreshed,
                        allowed_states={"worker_admitted"},
                        worker_url=normalized_worker_url,
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
                    accepted_status_transition = (
                        self._lease_settlement.commit_accepted_result(
                            task_id,
                            repos=repos,
                            phase=normalized_phase,
                            token=token,
                            bound=bound,
                            worker_url=worker_url,
                            request_fingerprint=request_fingerprint,
                            lease_binding=self._evaluate_lease_binding,
                        )
                    )
                    result_accepted = True
        if result_accepted:
            self._lease_settlement.notify_result_accepted(
                task_id,
                phase=normalized_phase,
                status_transition=accepted_status_transition,
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

        return admit_incoming_recovery_dispatch(
            task=task,
            token=token,
            phase=phase,
            admit_local=self.admit_dispatch_lease,
            request_fingerprint=request_fingerprint,
            timeout_seconds=timeout_seconds,
        )

    # -- Lease settlement (delegated) --------------------------------------

    def invalidate_task(
        self,
        task_id: str,
        *,
        reason_code: str,
    ) -> bool:
        return self._lease_settlement.invalidate_task(
            task_id,
            reason_code=reason_code,
        )

    def revoke_dispatch_lease(
        self,
        task_id: str,
        *,
        reason_code: str,
        app: Any | None = None,
    ) -> bool:
        """Revoke an in-flight Recovery capability under owner locks."""

        return self._lease_settlement.revoke_dispatch_lease(
            task_id,
            reason_code=reason_code,
            app=app,
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

        return self._lease_settlement.abort_dispatch_lease(
            task_id,
            target_status=target_status,
            reason_code=reason_code,
            error=error,
            app=app,
        )


_service = RecoveryDispatchGateService()


def get_recovery_dispatch_gate_service() -> (
    RecoveryDispatchGateService
):
    return _service
