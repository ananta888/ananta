"""Bounded Hub dispatcher and result collector for speech adaptation."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from agent.config import settings
from agent.repositories.speech_adaptation import SqlSpeechAdaptationDecisionStore
from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags
from agent.services.speech_adaptation_job_service import (
    SpeechAdaptationAdmissionError,
    SpeechAdaptationDecisionConflict,
    SpeechAdaptationJobService,
    SpeechPrincipal,
    restore_speech_adaptation_job,
)
from agent.services.speech_adaptation_task_port import HubSpeechAdaptationTaskPort
from agent.services.speech_adaptation_worker_port import (
    HttpSpeechAdaptationWorkerPort,
    SpeechAdaptationWorkerTransportError,
)

_EXTENSION_KEY = "speech_adaptation_dispatcher"
_TERMINAL = frozenset({"completed", "dataset_only", "cancelled", "failed", "denied"})


@dataclass(frozen=True, slots=True)
class SpeechAdaptationDispatchSummary:
    scanned: int = 0
    submitted: int = 0
    running: int = 0
    completed: int = 0
    cancelled: int = 0
    failed: int = 0
    retried: int = 0
    conflicts: int = 0
    unavailable: int = 0
    waiting_capacity: int = 0


class HubSpeechAdaptationDispatcher:
    """Own queue dispatch and polling; the worker never creates follow-up tasks."""

    def __init__(
        self,
        *,
        jobs: SqlSpeechAdaptationDecisionStore,
        service: SpeechAdaptationJobService,
        worker: HttpSpeechAdaptationWorkerPort,
        capacity,
        tasks: HubSpeechAdaptationTaskPort | None = None,
        artifacts=None,
        feature_enabled=None,
        clock_ms=None,
        max_dispatch_attempts: int = 8,
    ) -> None:
        if not 1 <= max_dispatch_attempts <= 32:
            raise ValueError("speech adaptation dispatch attempts are invalid")
        self._jobs = jobs
        self._service = service
        self._worker = worker
        self._capacity = capacity
        self._tasks = tasks or HubSpeechAdaptationTaskPort()
        self._artifacts = artifacts
        self._feature_enabled = feature_enabled or _feature_enabled
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._max_attempts = max_dispatch_attempts

    def run_once(self, *, batch_size: int = 32) -> SpeechAdaptationDispatchSummary:
        if not 1 <= batch_size <= 256:
            raise ValueError("speech adaptation dispatch batch is invalid")
        now = self._clock_ms()
        rows = self._jobs.list_dispatchable(now_ms=now, limit=batch_size)
        counts = {
            "submitted": 0,
            "running": 0,
            "completed": 0,
            "cancelled": 0,
            "failed": 0,
            "retried": 0,
            "conflicts": 0,
            "unavailable": 0,
            "waiting_capacity": 0,
        }
        enabled = bool(self._feature_enabled())
        for row in rows:
            try:
                principal = SpeechPrincipal(row.tenant_id, row.owner_subject)
                # Replaying the deterministic audit command on every observed
                # durable state repairs a prior audit outage without creating
                # duplicate events.
                self._audit_row(row)
                if not dict(row.contract_payload or {}):
                    if row.status != "queued":
                        raise SpeechAdaptationDecisionConflict("speech_capacity_wait_state_invalid")
                    if not enabled:
                        self._service.cancel(
                            principal,
                            row.id,
                            reason_code="speech_adaptation_feature_disabled",
                        )
                        counts["cancelled"] += 1
                        continue
                    request_payload = dict(row.admission_request_payload or {})
                    deadline = int(request_payload.get("deadline_at_ms") or 0)
                    if now >= deadline:
                        self._service.cancel(
                            principal,
                            row.id,
                            reason_code="speech_training_deadline_expired",
                        )
                        counts["cancelled"] += 1
                        continue
                    try:
                        promoted = self._service.promote_waiting(principal, row.id)
                    except SpeechAdaptationAdmissionError as exc:
                        self._service.cancel(
                            principal,
                            row.id,
                            reason_code=exc.reason_code,
                        )
                        counts["cancelled"] += 1
                        continue
                    if promoted.job is None:
                        waiting_row = self._jobs.transition_worker_state(
                            row.id,
                            expected_statuses=frozenset({"queued"}),
                            status="queued",
                            reason_code="speech_capacity_unavailable",
                            worker_status="waiting_capacity",
                            retry_delay_ms=1_000,
                        )
                        self._audit_row(waiting_row)
                        counts["waiting_capacity"] += 1
                        continue
                    row = self._jobs.get_row(row.id)
                    if row is None:
                        raise SpeechAdaptationDecisionConflict("speech_capacity_promotion_lost")
                    job = promoted.job
                else:
                    job = restore_speech_adaptation_job(dict(row.contract_payload or {}))
                if not enabled:
                    self._fence_terminal(
                        row,
                        job,
                        status="cancelled",
                        reason_code="speech_adaptation_feature_disabled",
                    )
                    counts["cancelled"] += 1
                    continue
                if now >= min(job.deadline_at_ms, job.fencing.lease_expires_at_ms):
                    self._cancel_worker_best_effort(job)
                    self._fence_terminal(
                        row,
                        job,
                        status="cancelled",
                        reason_code="speech_training_binding_expired",
                    )
                    counts["cancelled"] += 1
                    continue
                if row.status in {"queued", "dispatching"}:
                    self._submit(row, job)
                    counts["submitted"] += 1
                    continue
                if row.status == "cancel_requested":
                    self._worker.cancel(job, reason_code=row.reason_code)
                result = self._worker.result(job)
                if result is None:
                    if row.status != "cancel_requested":
                        running_row = self._jobs.transition_worker_state(
                            row.id,
                            expected_statuses=frozenset({"submitted", "running"}),
                            status="running",
                            reason_code="speech_training_running",
                            worker_status="running",
                            retry_delay_ms=250,
                        )
                        self._audit_row(running_row, job)
                        self._mark_task_running(running_row.task_id, job)
                    counts["running"] += 1
                    continue
                terminal = self._service.accept_result(principal, row.id, result)
                counts[terminal.status if terminal.status in counts else "completed"] += 1
            except SpeechAdaptationWorkerTransportError as exc:
                counts["unavailable"] += 1
                try:
                    self._handle_transport_failure(row, exc)
                    counts["retried" if exc.retryable else "failed"] += 1
                except SpeechAdaptationDecisionConflict:
                    counts["conflicts"] += 1
            except (SpeechAdaptationAdmissionError, SpeechAdaptationDecisionConflict, ValueError):
                counts["conflicts"] += 1
        return SpeechAdaptationDispatchSummary(scanned=len(rows), **counts)

    def fence_all(self, *, reason_code: str, batch_size: int = 256) -> int:
        fenced = 0
        for row in self._jobs.list_active(limit=batch_size):
            try:
                if not dict(row.contract_payload or {}):
                    self._service.cancel(
                        SpeechPrincipal(row.tenant_id, row.owner_subject),
                        row.id,
                        reason_code=reason_code,
                    )
                    fenced += 1
                    continue
                job = restore_speech_adaptation_job(dict(row.contract_payload or {}))
                self._cancel_worker_best_effort(job)
                self._fence_terminal(row, job, status="cancelled", reason_code=reason_code)
                fenced += 1
            except (SpeechAdaptationDecisionConflict, ValueError):
                continue
        return fenced

    def _submit(self, row, job) -> None:
        current = row
        if row.status == "queued":
            current = self._jobs.transition_worker_state(
                row.id,
                expected_statuses=frozenset({"queued"}),
                status="dispatching",
                reason_code="speech_training_dispatching",
                worker_status="dispatching",
                increment_dispatch_attempts=True,
            )
            self._audit_row(current, job)
        submission = self._worker.submit(job)
        submitted = self._jobs.transition_worker_state(
            row.id,
            expected_statuses=frozenset({"dispatching"}),
            status="submitted",
            reason_code="speech_training_submitted",
            worker_status=submission.status,
            retry_delay_ms=100,
        )
        self._audit_row(submitted, job)
        self._mark_task_running(row.task_id, job)
        del current

    def _handle_transport_failure(self, row, exc: SpeechAdaptationWorkerTransportError) -> None:
        current = self._jobs.get_row(row.id)
        if current is None or current.status in _TERMINAL:
            return
        if exc.retryable and current.dispatch_attempts < self._max_attempts:
            delay = min(30_000, 250 * 2 ** min(current.dispatch_attempts, 7))
            target = (
                "queued"
                if current.status == "dispatching"
                or (exc.reason_code == "speech_job_not_found" and current.status in {"submitted", "running"})
                else current.status
            )
            retrying = self._jobs.transition_worker_state(
                current.id,
                expected_statuses=frozenset({current.status}),
                status=target,
                reason_code=(current.reason_code if current.status == "cancel_requested" else exc.reason_code),
                worker_status="unavailable",
                retry_delay_ms=delay,
                increment_dispatch_attempts=True,
            )
            self._audit_row(retrying)
            return
        job = restore_speech_adaptation_job(dict(current.contract_payload or {}))
        cancellation_fence = current.status == "cancel_requested"
        self._fence_terminal(
            current,
            job,
            status="cancelled" if cancellation_fence else "failed",
            reason_code=(
                current.reason_code
                if cancellation_fence
                else "speech_worker_dispatch_retry_exhausted"
                if exc.retryable
                else exc.reason_code
            ),
        )

    def _fence_terminal(self, row, job, *, status: str, reason_code: str) -> None:
        saved = self._jobs.transition_worker_state(
            row.id,
            expected_statuses=frozenset({"queued", "dispatching", "submitted", "running", "cancel_requested"}),
            status=status,
            reason_code=reason_code,
            worker_status=status,
        )
        self._audit_row(saved, job)
        self._capacity.release(job.fencing.lease_id)
        if self._artifacts is not None:
            self._artifacts.reject_attempt(
                SpeechPrincipal(row.tenant_id, row.owner_subject),
                job,
            )
        self._tasks.finish(saved.task_id, status=status, reason_code=reason_code)

    def _cancel_worker_best_effort(self, job) -> None:
        try:
            self._worker.cancel(job, reason_code="speech_training_hub_fenced")
        except SpeechAdaptationWorkerTransportError:
            pass

    def _mark_task_running(self, task_id: str, job) -> None:
        marker = getattr(self._tasks, "mark_running", None)
        if callable(marker):
            try:
                marker(task_id, job=job)
            except Exception as exc:
                logging.warning(
                    "Speech adaptation task projection failed: %s",
                    type(exc).__name__,
                )

    def _audit_row(self, row, job=None) -> None:
        recorder = getattr(self._service, "record_worker_transition", None)
        if not callable(recorder):
            return
        if job is None and dict(row.contract_payload or {}):
            job = restore_speech_adaptation_job(dict(row.contract_payload or {}))
        recorder(
            SpeechPrincipal(row.tenant_id, row.owner_subject),
            job_id=row.id,
            status=row.status,
            reason_code=row.reason_code,
            job=job,
        )


def start_speech_adaptation_dispatcher_thread(app: Any) -> None:
    if settings.role != "hub":
        return
    composition = getattr(app, "extensions", {}).get("speech_adaptation_composition")
    if composition is None:
        return
    extensions = app.extensions
    state = extensions.get(_EXTENSION_KEY)
    if isinstance(state, Mapping):
        thread = state.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
    dispatcher = HubSpeechAdaptationDispatcher(
        jobs=composition.jobs,
        service=composition.service,
        worker=composition.worker,
        capacity=composition.capacity,
        artifacts=composition.artifacts,
        max_dispatch_attempts=_bounded_int("ANANTA_SPEECH_TRAINING_MAX_DISPATCH_ATTEMPTS", 8, 1, 32),
    )
    stop_event = threading.Event()
    interval = _bounded_float("ANANTA_SPEECH_TRAINING_DISPATCH_INTERVAL_SECONDS", 1.0, 0.1, 60.0)

    def run() -> None:
        import agent.common.context

        while not stop_event.is_set() and not agent.common.context.shutdown_requested:
            try:
                with app.app_context():
                    summary = dispatcher.run_once()
                if summary.scanned:
                    logging.info(
                        "Speech adaptation dispatch: scanned=%s submitted=%s running=%s "
                        "completed=%s cancelled=%s failed=%s retried=%s conflicts=%s "
                        "unavailable=%s waiting_capacity=%s",
                        summary.scanned,
                        summary.submitted,
                        summary.running,
                        summary.completed,
                        summary.cancelled,
                        summary.failed,
                        summary.retried,
                        summary.conflicts,
                        summary.unavailable,
                        summary.waiting_capacity,
                    )
            except Exception as exc:
                logging.warning(
                    "Speech adaptation dispatcher unavailable: %s",
                    type(exc).__name__,
                )
            stop_event.wait(interval)

    thread = threading.Thread(target=run, name="speech-adaptation-dispatcher", daemon=True)
    extensions[_EXTENSION_KEY] = {
        "dispatcher": dispatcher,
        "thread": thread,
        "stop_event": stop_event,
    }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_speech_adaptation_dispatcher(app: Any, *, join_timeout: float = 1.0) -> None:
    state = getattr(app, "extensions", {}).get(_EXTENSION_KEY)
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    thread = state.get("thread")
    dispatcher = state.get("dispatcher")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(dispatcher, HubSpeechAdaptationDispatcher):
        dispatcher.fence_all(reason_code="speech_training_hub_shutdown")
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=max(0.0, min(join_timeout, 10.0)))


def _feature_enabled() -> bool:
    return resolve_semantic_media_feature_flags(os.environ).get("speech_adaptation_training", False)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


__all__ = [
    "HubSpeechAdaptationDispatcher",
    "SpeechAdaptationDispatchSummary",
    "start_speech_adaptation_dispatcher_thread",
    "stop_speech_adaptation_dispatcher",
]
