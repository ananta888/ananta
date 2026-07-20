"""Hub-owned polling, heartbeat and result-admission loop."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.config import settings
from agent.repositories.speech_reconciliation import (
    SpeechReconciliationCollectibleAttempt,
    SpeechReconciliationRepository,
    SpeechReconciliationRepositoryError,
)
from agent.services.background.speech_reconciliation_queue_pump import (
    SpeechReconciliationResourceConfiguration,
    SpeechReconciliationWorkerConfiguration,
    SqlSpeechReconciliationRuntimePressureProbe,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.speech_reconciliation_production_composition import (
    SqlSpeechReconciliationDatasetPublisher,
    SqlTenantResolvingSpeechReconciliationLedgerLookup,
    SqlTenantResolvingSpeechReconciliationPublicationLedger,
)
from agent.services.speech_reconciliation_quality_controller import (
    HubSpeechReconciliationQualityController,
)
from agent.services.speech_reconciliation_resource_policy import (
    SpeechReconciliationResourcePolicy,
    SpeechReconciliationResourceRequest,
)
from agent.services.speech_reconciliation_result_admission_service import (
    HubSpeechReconciliationCurrentAuthority,
    HubSpeechReconciliationResultAdmissionService,
    HubSpeechReconciliationResultCollector,
    SpeechReconciliationResultAdmissionError,
)
from agent.services.speech_reconciliation_task_port import HubSpeechReconciliationTaskPort
from agent.services.speech_reconciliation_training_delegate import (
    RepositorySpeechReconciliationTrainingBudgetResolver,
    SpeechReconciliationTrainingDelegate,
)
from agent.services.speech_reconciliation_worker_port import (
    HttpSpeechReconciliationWorkerPort,
    SpeechReconciliationWorkerPort,
    SpeechReconciliationWorkerTransportError,
)
from agent.services.voice_governance_domain import VoicePrincipal

_EXTENSION_KEY = "speech_reconciliation_result_collector"


class SpeechReconciliationCollectibleAttemptPort(Protocol):
    def list_collectible_attempts(
        self,
        *,
        now_ms: int,
        limit: int,
    ) -> Sequence[SpeechReconciliationCollectibleAttempt]: ...

    def heartbeat(self, **values): ...

    def pause_active_attempt(self, **values) -> bool: ...

    def cancel_active_attempt(self, **values) -> bool: ...


@dataclass(frozen=True, slots=True)
class SpeechReconciliationCollectorSummary:
    scanned: int = 0
    pending: int = 0
    checkpointed: int = 0
    extended: int = 0
    completed: int = 0
    paused: int = 0
    cancelled: int = 0
    conflicts: int = 0
    unavailable: int = 0


class SqlSpeechReconciliationResultCollector:
    """Poll current DB attempts; workers can neither heartbeat nor publish directly."""

    def __init__(
        self,
        *,
        attempts: SpeechReconciliationCollectibleAttemptPort,
        worker: SpeechReconciliationWorkerPort,
        collector: HubSpeechReconciliationResultCollector,
        feature_enabled: Callable[[], bool],
        pause_reason: Callable[[object, int], str | None] | None = None,
        tasks: HubSpeechReconciliationTaskPort | None = None,
        clock_ms: Callable[[], int] | None = None,
        lease_ttl_ms: int = 30_000,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        if not 5_000 <= lease_ttl_ms <= 300_000:
            raise ValueError("speech_reconciliation_lease_ttl_invalid")
        self._attempts = attempts
        self._worker = worker
        self._collector = collector
        self._feature_enabled = feature_enabled
        self._pause_reason = pause_reason or (lambda _job, _now: None)
        self._tasks = tasks
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._lease_ttl_ms = lease_ttl_ms
        self._audit = audit

    def run_once(self, *, batch_size: int = 100) -> SpeechReconciliationCollectorSummary:
        if not 1 <= batch_size <= 1000:
            raise ValueError("speech_reconciliation_collector_batch_invalid")
        now = self._clock_ms()
        rows = tuple(self._attempts.list_collectible_attempts(now_ms=now, limit=batch_size))
        counts = {
            "pending": 0,
            "checkpointed": 0,
            "extended": 0,
            "completed": 0,
            "paused": 0,
            "cancelled": 0,
            "conflicts": 0,
            "unavailable": 0,
        }
        enabled = self._feature_enabled()
        for row in rows:
            job = row.job_contract
            principal = VoicePrincipal(row.tenant_id, row.owner_subject)
            if row.job_state == "cancel_requested":
                try:
                    self._worker.cancel(job)
                except SpeechReconciliationWorkerTransportError:
                    counts["unavailable"] += 1
                if self._attempts.cancel_active_attempt(
                    tenant_id=row.tenant_id,
                    owner_subject=row.owner_subject,
                    job_id=job.job_id,
                    attempt_id=job.attempt_id,
                    fencing_epoch=job.fencing_epoch,
                    reason_code="speech_reconciliation_cancelled",
                    now_ms=now,
                ):
                    self._record_audit(row, job, "cancelled", "speech_reconciliation_cancelled")
                    counts["cancelled"] += 1
                    self._finish_tasks(job, status="cancelled", reason_code="speech_reconciliation_cancelled")
                else:
                    counts["conflicts"] += 1
                continue
            pause_reason = "speech_reconciliation_feature_disabled" if not enabled else self._pause_reason(job, now)
            if pause_reason is not None:
                try:
                    self._worker.cancel(job)
                except SpeechReconciliationWorkerTransportError:
                    counts["unavailable"] += 1
                if self._attempts.pause_active_attempt(
                    tenant_id=row.tenant_id,
                    owner_subject=row.owner_subject,
                    job_id=job.job_id,
                    attempt_id=job.attempt_id,
                    fencing_epoch=job.fencing_epoch,
                    reason_code=pause_reason,
                    now_ms=now,
                ):
                    self._record_audit(row, job, "paused", pause_reason)
                    counts["paused"] += 1
                    self._cancel_attempt_task(job, reason_code=pause_reason)
                else:
                    counts["conflicts"] += 1
                continue
            try:
                admission = self._collector.collect(principal, job)
                if admission.disposition == "pending":
                    heartbeat_now = self._clock_ms()
                    self._attempts.heartbeat(
                        job_id=job.job_id,
                        attempt_id=job.attempt_id,
                        fencing_epoch=job.fencing_epoch,
                        fencing_token_digest=job.fencing_token_digest,
                        expected_version=row.attempt_version,
                        lease_expires_at_ms=min(
                            heartbeat_now + self._lease_ttl_ms,
                            job.deadline_at_ms,
                        ),
                        now_ms=heartbeat_now,
                    )
                    counts["pending"] += 1
                elif admission.disposition == "checkpointed":
                    counts["checkpointed"] += 1
                elif admission.disposition == "extended":
                    counts["extended"] += 1
                    self._finish_attempt_task(
                        job,
                        status="completed",
                        reason_code=admission.reason_code,
                    )
                else:
                    counts["completed"] += 1
                    result_status = admission.result.status if admission.result is not None else "completed"
                    terminal = result_status if result_status in {"failed", "cancelled"} else "completed"
                    self._finish_tasks(job, status=terminal, reason_code=admission.reason_code)
            except SpeechReconciliationWorkerTransportError:
                # Do not renew a lease after an unavailable or invalid worker
                # response.  The durable recovery loop will fence it.
                counts["unavailable"] += 1
            except (SpeechReconciliationRepositoryError, SpeechReconciliationResultAdmissionError):
                counts["conflicts"] += 1
        return SpeechReconciliationCollectorSummary(scanned=len(rows), **counts)

    def _cancel_attempt_task(self, job, *, reason_code: str) -> None:
        if self._tasks is None:
            return
        self._tasks.cancel(
            self._tasks.attempt_task_id(job.job_id, job.attempt_id, job.fencing_epoch),
            reason_code=reason_code,
        )

    def _finish_tasks(self, job, *, status: str, reason_code: str) -> None:
        if self._tasks is None:
            return
        self._tasks.finish(
            self._tasks.attempt_task_id(job.job_id, job.attempt_id, job.fencing_epoch),
            status=status,
            reason_code=reason_code,
        )
        self._tasks.finish(
            self._tasks.parent_task_id(job.job_id),
            status=status,
            reason_code=reason_code,
        )

    def _finish_attempt_task(self, job, *, status: str, reason_code: str) -> None:
        if self._tasks is None:
            return
        self._tasks.finish(
            self._tasks.attempt_task_id(job.job_id, job.attempt_id, job.fencing_epoch),
            status=status,
            reason_code=reason_code,
        )

    def pause_all(self, *, reason_code: str, batch_size: int = 1000) -> int:
        """Best-effort bounded shutdown fence; DB authority is revoked even if HTTP fails."""

        now = self._clock_ms()
        rows = tuple(self._attempts.list_collectible_attempts(now_ms=now, limit=batch_size))
        paused = 0
        for row in rows:
            job = row.job_contract
            try:
                self._worker.cancel(job)
            except SpeechReconciliationWorkerTransportError:
                pass
            values = {
                "tenant_id": row.tenant_id,
                "owner_subject": row.owner_subject,
                "job_id": job.job_id,
                "attempt_id": job.attempt_id,
                "fencing_epoch": job.fencing_epoch,
                "now_ms": now,
            }
            if row.job_state == "cancel_requested":
                fenced = self._attempts.cancel_active_attempt(
                    **values,
                    reason_code="speech_reconciliation_cancelled",
                )
            else:
                fenced = self._attempts.pause_active_attempt(
                    **values,
                    reason_code=reason_code,
                )
            if fenced:
                self._record_audit(
                    row,
                    job,
                    "cancelled" if row.job_state == "cancel_requested" else "paused",
                    "speech_reconciliation_cancelled" if row.job_state == "cancel_requested" else reason_code,
                )
                paused += 1
                if row.job_state == "cancel_requested":
                    self._finish_tasks(
                        job,
                        status="cancelled",
                        reason_code="speech_reconciliation_cancelled",
                    )
                else:
                    self._cancel_attempt_task(job, reason_code=reason_code)
        return paused

    def _record_audit(self, row, job, transition: str, reason_code: str) -> None:
        if self._audit is None or bool(getattr(self._attempts, "transactional_audit", False)):
            return
        event = self._audit.prepare_transition(
            idempotency_key=(
                f"speech-reconciliation-collector:{job.job_id}:"
                f"{job.attempt_id}:{transition}:{reason_code}"
            ),
            tenant_id=row.tenant_id,
            scope=f"speech-job:{job.job_id}",
            event_type="semantic_job",
            transition=transition,
            reason_code=reason_code,
            epoch=job.fencing_epoch,
            lease_ref=job.attempt_id,
            job_ref=job.job_id,
        )
        self._audit.append_prepared(event)


class _UnavailableSpeechReconciliationWorker:
    """Fail-closed transport that still permits DB-only startup fencing."""

    @staticmethod
    def _raise():
        raise SpeechReconciliationWorkerTransportError("speech_reconciliation_worker_unavailable")

    def submit(self, _task):
        self._raise()

    def upload_audio(self, _task, _ciphertext):
        self._raise()

    def poll(self, _job):
        self._raise()

    def cancel(self, _job):
        self._raise()


class _UnavailableSpeechTrainingAdmission:
    def admit_dataset(self, _principal, **_values):
        raise ValueError("speech_reconciliation_training_admission_unavailable")


def build_speech_reconciliation_result_collector(
    *,
    audit=None,
    training_admission=None,
) -> SqlSpeechReconciliationResultCollector:
    try:
        configuration = SpeechReconciliationWorkerConfiguration.from_environment(os.environ)
        worker: SpeechReconciliationWorkerPort = HttpSpeechReconciliationWorkerPort(
            endpoint=configuration.endpoint,
            allowed_endpoints=configuration.allowed_endpoints,
            bearer_token=configuration.bearer_token,
        )
    except Exception:
        worker = _UnavailableSpeechReconciliationWorker()
    repository = SpeechReconciliationRepository(audit=audit)
    resources = SpeechReconciliationResourceConfiguration.from_environment(os.environ)
    pressure = SqlSpeechReconciliationRuntimePressureProbe(
        charging=_strict_environment_boolean(
            os.environ.get("ANANTA_SPEECH_RECONCILIATION_CHARGING"),
            default=False,
        ),
        quiet_hours=_strict_environment_boolean(
            os.environ.get("ANANTA_SPEECH_RECONCILIATION_QUIET_HOURS"),
            default=False,
        ),
    )
    policy = SpeechReconciliationResourcePolicy()

    def pause_reason(job, now_ms: int) -> str | None:
        snapshot = pressure.snapshot(now_ms=now_ms)
        decision = policy.evaluate(
            SpeechReconciliationResourceRequest(
                mode=resources.mode,
                requested_factor=job.max_compute_factor,
                user_max_factor=resources.user_max_factor,
                live_call_active=snapshot.live_call_active,
                foreground_load_micros=snapshot.foreground_load_micros,
                charging=snapshot.charging,
                minute_of_day=snapshot.minute_of_day,
                schedule_start_minute=resources.schedule_start_minute,
                schedule_end_minute=resources.schedule_end_minute,
                quiet_hours=snapshot.quiet_hours,
            )
        )
        return None if decision.allowed else decision.reason_code

    admission = HubSpeechReconciliationResultAdmissionService(
        authority=HubSpeechReconciliationCurrentAuthority(
            jobs=repository,
            consents=SpeechEvidenceConsentService(),
        ),
        repository=repository,
        ledger=SqlTenantResolvingSpeechReconciliationPublicationLedger(),
        publisher=SqlSpeechReconciliationDatasetPublisher(audit=audit),
        quality=HubSpeechReconciliationQualityController(
            repository=repository,
            ledgers=SqlTenantResolvingSpeechReconciliationLedgerLookup(),
        ),
        training=SpeechReconciliationTrainingDelegate(
            training_admission or _UnavailableSpeechTrainingAdmission()
        ),
        training_budgets=RepositorySpeechReconciliationTrainingBudgetResolver(repository),
        audit=audit,
    )
    return SqlSpeechReconciliationResultCollector(
        attempts=repository,
        worker=worker,
        collector=HubSpeechReconciliationResultCollector(
            worker=worker,
            admission=admission,
        ),
        feature_enabled=lambda: resolve_semantic_media_feature_flags(os.environ).get("speech_reconciliation", False),
        pause_reason=pause_reason,
        tasks=HubSpeechReconciliationTaskPort(),
        lease_ttl_ms=_environment_int(
            "ANANTA_SPEECH_RECONCILIATION_LEASE_TTL_MS",
            30_000,
            minimum=5_000,
            maximum=300_000,
        ),
        audit=audit,
    )


def start_speech_reconciliation_result_collector_thread(app: Any) -> None:
    # Start while disabled as well: persisted work from a previous process
    # must be fenced when an operator activates the kill switch.
    if settings.role != "hub":
        return
    extensions = getattr(app, "extensions", None)
    if isinstance(extensions, dict):
        existing = extensions.get(_EXTENSION_KEY)
        if isinstance(existing, Mapping):
            thread = existing.get("thread")
            if isinstance(thread, threading.Thread) and thread.is_alive():
                return
    try:
        factory = (
            extensions.get("speech_reconciliation_result_collector_factory") if isinstance(extensions, dict) else None
        )
        service = factory() if callable(factory) else build_speech_reconciliation_result_collector(
            audit=(extensions.get("semantic_media_audit_recorder") if isinstance(extensions, dict) else None),
            training_admission=(
                extensions.get("speech_reconciliation_training_admission")
                if isinstance(extensions, dict)
                else None
            ),
        )
    except Exception as exc:
        logging.warning(
            "Speech reconciliation result collector unavailable: %s",
            str(getattr(exc, "reason_code", type(exc).__name__)),
        )
        return
    interval = _environment_float(
        "ANANTA_SPEECH_RECONCILIATION_COLLECT_INTERVAL_SECONDS",
        2.0,
        minimum=0.25,
        maximum=300.0,
    )
    batch = _environment_int(
        "ANANTA_SPEECH_RECONCILIATION_COLLECT_BATCH_SIZE",
        100,
        minimum=1,
        maximum=1000,
    )
    stop_event = threading.Event()

    def run() -> None:
        import agent.common.context

        while not stop_event.is_set() and not agent.common.context.shutdown_requested:
            try:
                with app.app_context():
                    summary = service.run_once(batch_size=batch)
                if summary.completed or summary.paused or summary.cancelled or summary.conflicts or summary.unavailable:
                    logging.info(
                        "Speech reconciliation collector: scanned=%s pending=%s "
                        "completed=%s paused=%s cancelled=%s conflicts=%s unavailable=%s",
                        summary.scanned,
                        summary.pending,
                        summary.completed,
                        summary.paused,
                        summary.cancelled,
                        summary.conflicts,
                        summary.unavailable,
                    )
            except Exception as exc:
                logging.warning(
                    "Speech reconciliation collector tick unavailable: %s",
                    str(getattr(exc, "reason_code", type(exc).__name__)),
                )
            stop_event.wait(interval)

    thread = threading.Thread(
        target=run,
        name="speech-reconciliation-result-collector",
        daemon=True,
    )
    if isinstance(extensions, dict):
        extensions[_EXTENSION_KEY] = {
            "service": service,
            "thread": thread,
            "stop_event": stop_event,
        }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_speech_reconciliation_result_collector(
    app: Any,
    *,
    join_timeout: float = 1.0,
) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, Mapping) else None
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    thread = state.get("thread")
    service = state.get("service")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if hasattr(service, "pause_all"):
        try:
            with app.app_context():
                service.pause_all(reason_code="speech_reconciliation_shutdown")
        except Exception as exc:
            logging.warning(
                "Speech reconciliation shutdown fence unavailable: %s",
                str(getattr(exc, "reason_code", type(exc).__name__)),
            )
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=max(0.0, min(float(join_timeout), 10.0)))


def _environment_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name) or default), 10)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _environment_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default
    if value != value or value in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(value, maximum))


def _strict_environment_boolean(value: object, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    raise ValueError("speech_reconciliation_environment_boolean_invalid")


__all__ = [
    "SpeechReconciliationCollectibleAttemptPort",
    "SpeechReconciliationCollectorSummary",
    "SqlSpeechReconciliationResultCollector",
    "build_speech_reconciliation_result_collector",
    "start_speech_reconciliation_result_collector_thread",
    "stop_speech_reconciliation_result_collector",
]
