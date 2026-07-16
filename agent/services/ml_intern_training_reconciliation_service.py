"""Hub-owned recovery policy for durable LoRA training jobs.

The reconciler only repairs control-plane state and delegates retries back to
the Hub control service.  It never executes training work or imports an ML
runtime.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.db_models import (
    MlInternTrainingAttemptDB,
    MlInternTrainingEventDB,
    MlInternTrainingJobDB,
)
from agent.repositories.ml_intern_training import MlInternTrainingRepositoryConflict
from agent.services.ml_intern_training_contract import assert_job_transition, sanitize_event_payload
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal

_ACTIVE_ATTEMPT_STATUSES = frozenset({"claimed", "running"})
_TERMINAL_ATTEMPT_STATUSES = frozenset({"cancelled", "completed", "failed", "interrupted"})
_AUDIT_ACTOR = "hub:ml-intern-training-reconciler"


class MlInternTrainingReconciliationRepositoryPort(Protocol):
    """Small persistence interface used by the Hub recovery loop (ISP)."""

    def list_active_jobs(self, *, limit: int = 1000) -> list[MlInternTrainingJobDB]: ...

    def get_attempt(self, attempt_id: str) -> MlInternTrainingAttemptDB | None: ...

    def list_attempts(self, job_id: str, *, limit: int = 100) -> list[MlInternTrainingAttemptDB]: ...

    def save_job(self, job: MlInternTrainingJobDB, *, expected_version: int) -> MlInternTrainingJobDB: ...

    def save_attempt(
        self,
        attempt: MlInternTrainingAttemptDB,
        *,
        expected_version: int,
    ) -> MlInternTrainingAttemptDB: ...

    def append_event(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        *,
        event_type: str,
        dedupe_key: str,
        payload: dict,
    ) -> MlInternTrainingEventDB: ...


class MlInternTrainingRecoverySchedulerPort(Protocol):
    """Hub delegation seam; recovery must not call worker runtimes directly."""

    def schedule_reconciled_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> bool: ...

    def begin_shutdown(self) -> None: ...


class MlInternTrainingAuditPort(Protocol):
    def __call__(self, action: str, details: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class MlInternTrainingReconciliationPolicy:
    """Explicit, bounded policy for stale leases and retry/resume decisions."""

    heartbeat_timeout_seconds: float = 120.0
    queued_stale_seconds: float = 30.0
    cancel_grace_seconds: float = 30.0
    max_attempts: int = 3
    retry_stale_attempts: bool = True
    resume_from_checkpoint: bool = True
    batch_limit: int = 100

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "MlInternTrainingReconciliationPolicy":
        raw = dict(value or {})
        nested = raw.get("reconciliation")
        source = dict(nested) if isinstance(nested, Mapping) else raw
        return cls(
            heartbeat_timeout_seconds=_bounded_float(
                source.get("heartbeat_timeout_seconds"),
                default=120.0,
                minimum=5.0,
                maximum=86_400.0,
            ),
            queued_stale_seconds=_bounded_float(
                source.get("queued_stale_seconds"),
                default=30.0,
                minimum=1.0,
                maximum=86_400.0,
            ),
            cancel_grace_seconds=_bounded_float(
                source.get("cancel_grace_seconds"),
                default=30.0,
                minimum=1.0,
                maximum=86_400.0,
            ),
            max_attempts=_bounded_int(
                source.get("max_attempts", source.get("max_recovery_attempts")),
                default=3,
                minimum=1,
                maximum=32,
            ),
            retry_stale_attempts=_as_bool(source.get("retry_stale_attempts"), default=True),
            resume_from_checkpoint=_as_bool(source.get("resume_from_checkpoint"), default=True),
            batch_limit=_bounded_int(
                source.get("batch_limit"),
                default=100,
                minimum=1,
                maximum=500,
            ),
        )


class MlInternTrainingReconciliationService:
    """Reconcile a bounded slice of stale persistent jobs on the Hub."""

    def __init__(
        self,
        repository: MlInternTrainingReconciliationRepositoryPort,
        scheduler: MlInternTrainingRecoverySchedulerPort,
        *,
        policy: MlInternTrainingReconciliationPolicy | None = None,
        audit: MlInternTrainingAuditPort | None = None,
        clock: Callable[[], float] = time.time,
        is_hub: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._policy = policy or MlInternTrainingReconciliationPolicy()
        self._audit = audit or _default_audit
        self._clock = clock
        self._is_hub = is_hub or _configured_as_hub
        self._draining = threading.Event()

    def begin_shutdown(self) -> None:
        """Stop retry claims; running worker leases remain durable and expire normally."""

        self._draining.set()
        try:
            self._scheduler.begin_shutdown()
        except Exception:
            # Shutdown must remain best-effort and must not expose runtime details.
            return

    def run_once(self, *, limit: int | None = None) -> dict[str, Any]:
        """Repair at most ``limit`` oldest active jobs without executing worker work."""

        summary = {
            "hub_only": True,
            "draining": self._draining.is_set(),
            "scanned": 0,
            "processed": 0,
            "reconciled": 0,
            "retried": 0,
            "cancelled": 0,
            "failed": 0,
            "redispatched": 0,
            "deferred": 0,
            "conflicts": 0,
            "errors": [],
        }
        if not self._is_hub():
            summary["hub_only"] = False
            return summary
        if self._draining.is_set():
            return summary

        bounded = max(1, min(int(limit or self._policy.batch_limit), 500))
        jobs = self._repository.list_active_jobs(limit=bounded)
        summary["scanned"] = len(jobs)
        now = float(self._clock())
        for job in jobs:
            if self._draining.is_set():
                summary["draining"] = True
                break
            try:
                outcome = self._reconcile_job(job, now=now)
                if outcome is None:
                    continue
                summary["processed"] += 1
                summary["reconciled"] += 1
                summary[outcome] += 1
            except MlInternTrainingRepositoryConflict:
                # Another Hub transaction won the optimistic-CAS race.  Its
                # durable state is authoritative; the next bounded tick verifies it.
                summary["processed"] += 1
                summary["conflicts"] += 1
            except Exception as exc:
                summary["processed"] += 1
                summary["failed"] += 1
                summary["errors"].append(
                    {
                        "job_id": job.id,
                        "reason_code": "reconciliation_failed",
                        "error_type": type(exc).__name__,
                    }
                )
        summary["draining"] = self._draining.is_set()
        return summary

    def _reconcile_job(self, job: MlInternTrainingJobDB, *, now: float) -> str | None:
        if job.status == "queued":
            if job.updated_at > now - self._policy.queued_stale_seconds:
                return None
            return self._redispatch_queued(job, now=now)

        attempt = self._resolve_attempt(job)
        if job.status == "cancel_requested":
            if job.updated_at > now - self._policy.cancel_grace_seconds:
                return None
            return self._recover_attempt(
                job,
                attempt,
                now=now,
                reason_code="cancel_deadline_exceeded",
                cancel=True,
            )
        if job.status == "interrupted":
            return self._recover_attempt(
                job,
                attempt,
                now=now,
                reason_code=job.error_code or "interrupted_recovery_resumed",
                cancel=job.cancel_requested,
            )

        reason_code = self._stale_reason(job, attempt, now=now)
        if reason_code is None:
            return None
        return self._recover_attempt(
            job,
            attempt,
            now=now,
            reason_code=reason_code,
            cancel=False,
        )

    def _resolve_attempt(self, job: MlInternTrainingJobDB) -> MlInternTrainingAttemptDB | None:
        if job.active_attempt_id:
            attempt = self._repository.get_attempt(job.active_attempt_id)
            if attempt is not None and attempt.job_id == job.id:
                return attempt
        attempts = self._repository.list_attempts(job.id, limit=32)
        return attempts[0] if attempts else None

    def _stale_reason(
        self,
        job: MlInternTrainingJobDB,
        attempt: MlInternTrainingAttemptDB | None,
        *,
        now: float,
    ) -> str | None:
        stale_before = now - self._policy.heartbeat_timeout_seconds
        if attempt is None:
            return "active_attempt_missing" if job.updated_at <= stale_before else None
        if attempt.status == "interrupted":
            return attempt.error_code or "attempt_already_interrupted"
        if attempt.status not in _ACTIVE_ATTEMPT_STATUSES:
            return "attempt_not_active" if job.updated_at <= stale_before else None
        if attempt.lease_expires_at <= now:
            return "attempt_lease_expired"
        if attempt.last_heartbeat_at <= stale_before:
            return "attempt_heartbeat_stale"
        return None

    def _redispatch_queued(self, job: MlInternTrainingJobDB, *, now: float) -> str:
        expected = job.version
        job.phase = "recovery_queued"
        job.queue_position = None
        saved = self._repository.save_job(job, expected_version=expected)
        self._append_event(
            saved,
            event_type="recovery_queued",
            dedupe_key=f"reconcile-queued-{saved.version}",
            payload={
                "status": "queued",
                "phase": saved.phase,
                "reason_code": "stale_queued_job",
                "progress_percent": saved.progress_percent,
            },
        )
        scheduled = self._scheduler.schedule_reconciled_job(
            MlInternTrainingPrincipal(saved.tenant_id, saved.owner_subject),
            saved.id,
        )
        outcome = "redispatched" if scheduled else "deferred"
        self._emit_audit(
            saved,
            attempt=None,
            reason_code="stale_queued_job",
            from_status="queued",
            to_status="queued",
            outcome=outcome,
        )
        return outcome

    def _recover_attempt(
        self,
        job: MlInternTrainingJobDB,
        attempt: MlInternTrainingAttemptDB | None,
        *,
        now: float,
        reason_code: str,
        cancel: bool,
    ) -> str:
        attempt = self._interrupt_and_fence_attempt(attempt, now=now, reason_code=reason_code)
        attempts = self._repository.list_attempts(job.id, limit=32)
        attempt_number = max((item.attempt_number for item in attempts), default=0)
        checkpoint_ref = next(
            (
                value
                for value in (
                    attempt.checkpoint_ref if attempt is not None else None,
                    job.checkpoint_ref,
                )
                if value
            ),
            None,
        )

        from_status = job.status
        assert_job_transition(from_status, "interrupted")
        expected = job.version
        job.status = "interrupted"
        job.phase = "interrupted"
        job.active_attempt_id = None
        job.worker_job_id = None
        job.queue_position = None
        job.checkpoint_ref = checkpoint_ref
        job.error_code = reason_code[:128]
        job.error_message = None
        job.retryable = not cancel and self._retry_allowed(attempt_number)
        interrupted = self._repository.save_job(job, expected_version=expected)
        self._append_event(
            interrupted,
            event_type="interrupted",
            dedupe_key=f"reconcile-interrupted-{interrupted.version}",
            payload={
                "status": "interrupted",
                "phase": "interrupted",
                "reason_code": reason_code,
                "checkpoint_ref": checkpoint_ref,
                "retryable": interrupted.retryable,
            },
        )

        if cancel:
            interrupted.result_summary = {
                **dict(interrupted.result_summary or {}),
                "cancel_mode": "forced",
            }
            terminal = self._save_status(
                interrupted,
                target="cancelled",
                phase="cancelled",
                now=now,
                error_code="cancel_deadline_exceeded",
                retryable=False,
                terminal=True,
            )
            terminal.cancel_requested = True
            self._append_event(
                terminal,
                event_type="cancelled",
                dedupe_key=f"reconcile-cancelled-{terminal.version}",
                payload={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "reason_code": "cancel_deadline_exceeded",
                    "cancel_mode": "forced",
                    "progress_percent": terminal.progress_percent,
                },
            )
            self._emit_audit(
                terminal,
                attempt=attempt,
                reason_code=reason_code,
                from_status=from_status,
                to_status="cancelled",
                outcome="cancelled",
            )
            return "cancelled"

        if self._retry_allowed(attempt_number):
            retry_phase = (
                "retry_queued_resume" if checkpoint_ref and self._policy.resume_from_checkpoint else "retry_queued"
            )
            queued = self._save_status(
                interrupted,
                target="queued",
                phase=retry_phase,
                now=now,
                error_code=reason_code,
                retryable=True,
                terminal=False,
                reset_progress=True,
            )
            self._append_event(
                queued,
                event_type="retry_queued",
                dedupe_key=f"reconcile-retry-{queued.version}",
                payload={
                    "status": "queued",
                    "phase": queued.phase,
                    "reason_code": reason_code,
                    "checkpoint_ref": checkpoint_ref,
                    "retryable": True,
                    "progress_percent": queued.progress_percent,
                },
            )
            scheduled = self._scheduler.schedule_reconciled_job(
                MlInternTrainingPrincipal(queued.tenant_id, queued.owner_subject),
                queued.id,
            )
            outcome = "retried" if scheduled else "deferred"
            self._emit_audit(
                queued,
                attempt=attempt,
                reason_code=reason_code,
                from_status=from_status,
                to_status="queued",
                outcome=outcome,
            )
            return outcome

        exhausted_reason = (
            "recovery_retry_budget_exhausted" if self._policy.retry_stale_attempts else "recovery_retry_disabled"
        )
        terminal = self._save_status(
            interrupted,
            target="failed",
            phase="failed",
            now=now,
            error_code=exhausted_reason,
            retryable=False,
            terminal=True,
        )
        self._append_event(
            terminal,
            event_type="failed",
            dedupe_key=f"reconcile-failed-{terminal.version}",
            payload={
                "status": "failed",
                "phase": "failed",
                "reason_code": exhausted_reason,
                "progress_percent": terminal.progress_percent,
                "retryable": False,
            },
        )
        self._emit_audit(
            terminal,
            attempt=attempt,
            reason_code=exhausted_reason,
            from_status=from_status,
            to_status="failed",
            outcome="failed",
        )
        return "failed"

    def _interrupt_and_fence_attempt(
        self,
        attempt: MlInternTrainingAttemptDB | None,
        *,
        now: float,
        reason_code: str,
    ) -> MlInternTrainingAttemptDB | None:
        if attempt is None or attempt.status in _TERMINAL_ATTEMPT_STATUSES:
            return attempt
        expected = attempt.version
        attempt.status = "interrupted"
        attempt.error_code = reason_code[:128]
        attempt.lease_expires_at = now
        attempt.finished_at = now
        # Rotating the digest invalidates the previous worker token without
        # persisting either the raw token or any training content.
        attempt.fencing_token_digest = hashlib.sha256(
            f"revoked\0{attempt.id}\0{expected}\0{now}".encode("utf-8")
        ).hexdigest()
        return self._repository.save_attempt(attempt, expected_version=expected)

    def _retry_allowed(self, latest_attempt_number: int) -> bool:
        return self._policy.retry_stale_attempts and latest_attempt_number < self._policy.max_attempts

    def _save_status(
        self,
        job: MlInternTrainingJobDB,
        *,
        target: str,
        phase: str,
        now: float,
        error_code: str,
        retryable: bool,
        terminal: bool,
        reset_progress: bool = False,
    ) -> MlInternTrainingJobDB:
        assert_job_transition(job.status, target)
        expected = job.version
        job.status = target
        job.phase = phase
        job.error_code = error_code[:128]
        job.error_message = None
        job.retryable = retryable
        job.active_attempt_id = None
        job.worker_job_id = None
        job.queue_position = None
        if reset_progress:
            job.progress_percent = 0.0
            job.current_step = None
            job.epoch = None
            job.train_loss = None
            job.eval_loss = None
            job.learning_rate = None
            job.finished_at = None
            job.cancel_requested = False
        if terminal:
            job.progress_percent = 100.0
            job.finished_at = now
        return self._repository.save_job(job, expected_version=expected)

    def _append_event(
        self,
        job: MlInternTrainingJobDB,
        *,
        event_type: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
    ) -> None:
        self._repository.append_event(
            MlInternTrainingPrincipal(job.tenant_id, job.owner_subject),
            job.id,
            event_type=event_type,
            dedupe_key=dedupe_key,
            payload=sanitize_event_payload(payload),
        )

    def _emit_audit(
        self,
        job: MlInternTrainingJobDB,
        *,
        attempt: MlInternTrainingAttemptDB | None,
        reason_code: str,
        from_status: str,
        to_status: str,
        outcome: str,
    ) -> None:
        details = {
            "actor": _AUDIT_ACTOR,
            "reason_code": reason_code[:128],
            "tenant_id": job.tenant_id,
            "task_id": job.task_id,
            "job_id": job.id,
            "attempt_id": attempt.id if attempt is not None else None,
            "attempt_number": attempt.attempt_number if attempt is not None else None,
            "from_status": from_status,
            "to_status": to_status,
            "outcome": outcome,
        }
        try:
            self._audit("ml_intern_training_reconciled", details)
        except Exception:
            # Recovery state is authoritative; an unavailable audit sink must
            # not revive a fenced lease or leak an exception payload.
            return


def build_ml_intern_training_reconciliation_service(
    config: Mapping[str, Any] | None = None,
    *,
    repository: MlInternTrainingReconciliationRepositoryPort | None = None,
    scheduler: MlInternTrainingRecoverySchedulerPort | None = None,
) -> MlInternTrainingReconciliationService:
    """Compose the Hub service from persistence and control-plane adapters."""

    if repository is None:
        from agent.repositories.ml_intern_training import get_ml_intern_training_repository

        repository = get_ml_intern_training_repository()
    if scheduler is None:
        from agent.services.ml_intern_training_control_service import (
            get_ml_intern_training_control_service,
        )

        scheduler = get_ml_intern_training_control_service(config)
    return MlInternTrainingReconciliationService(
        repository,
        scheduler,
        policy=MlInternTrainingReconciliationPolicy.from_mapping(config),
    )


def _default_audit(action: str, details: dict[str, Any]) -> None:
    from agent.common.audit import log_audit

    log_audit(action, details)


def _configured_as_hub() -> bool:
    from agent.config import settings

    return settings.role == "hub"


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


__all__ = [
    "MlInternTrainingReconciliationPolicy",
    "MlInternTrainingReconciliationRepositoryPort",
    "MlInternTrainingReconciliationService",
    "MlInternTrainingRecoverySchedulerPort",
    "build_ml_intern_training_reconciliation_service",
]
